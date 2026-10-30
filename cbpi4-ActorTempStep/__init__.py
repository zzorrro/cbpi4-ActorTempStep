import asyncio
import aiohttp
import logging
from cbpi.api import *
from cbpi.api.step import CBPiStep, StepResult
from cbpi.api.timer import Timer
from cbpi.api.dataclasses import NotificationType

logger = logging.getLogger(__name__)

@parameters([
    Property.Kettle(label="Kettle", description="Boiling kettle"),
    Property.Sensor(label="Sensor", description="Temperature sensor to monitor"),
    Property.Select(label="AutoMode", options=["Yes", "No"], description="Enable automatic heating control (PID/hysteresis) -> Yes"),
    Property.Number(label="Temp", description="Target temperature to trigger the actor (°C)", configurable=True),
    Property.Number(label="Delay", description="Delay in minutes before executing action (0 = no delay)", configurable=True),
    Property.Actor(label="Actor", description="Actor to control when target temp is reached"),
    Property.Select(label="Action", options=["Turn ON", "Turn OFF"], description="Action to perform when target temp is reached"),
    Property.Select(label="AutoMode Action", options=["Leave ON", "Turn OFF"], description="Action for AutoMode when target temp is reached")
])
class ActorTempStep(CBPiStep):

    async def on_start(self):
        """Инициализация при запуске шага"""
        self.port = str(self.cbpi.static_config.get('port', 8000))
        
        # 1. Получение параметров
        self.kettle = self.get_kettle(self.props.Kettle)
        self.sensor = self.props.Sensor
        self.AutoMode = True if self.props.get("AutoMode", "No") == "Yes" else False
        self.actor = self.props.get("Actor", None)
        self.action_type = self.props.get("Action", "Turn ON")
        self.automode_action = self.props.get("AutoMode Action", "Leave ON")
        
        self.delay_minutes = int(self.props.get("Delay", 0))
        self.delay_seconds = self.delay_minutes * 60
        
        # Флаги состояния
        self.temp_reached = False
        
        # 2. Устанавливаем системный параметр целевой температуры котла
        if self.kettle is not None:
            self.kettle.target_temp = int(self.props.get("Temp", 0))
            
        # 3. Инициализация таймера задержки (ТОЛЬКО если задержка > 0)
        if self.delay_seconds > 0:
            self.delay_timer = Timer(self.delay_seconds, on_update=self.on_delay_timer_update, on_done=self.on_delay_timer_done)
        else:
            self.delay_timer = None
            
        # 4. Активация AutoMode на старте, если выбран
        if self.AutoMode == True:
            await self.setAutoMode(True)
            logger.info("AutoMode activated at step start")
            
        self.summary = f"Waiting for {self.kettle.target_temp}°C..."
        await self.push_update()

    async def on_delay_timer_update(self, timer, seconds):
        """Обновление таймера задержки на экране (только если Delay > 0)"""
        if self.delay_minutes > 0:
            self.summary = f"Delay: {Timer.format_time(seconds)}"
            await self.push_update()

    async def on_delay_timer_done(self, timer):
        """Действие при завершении таймера задержки"""
        await self.execute_action()

    async def execute_action(self):
        """Выполнение действий с актором и AutoMode"""
        # 1. Выполняем действие с актором
        if self.actor is not None:
            current_state = getattr(self.actor, 'state', False)
            
            if self.action_type == "Turn ON":
                if not current_state:
                    await self.actor_on(self.actor)
                    logger.info(f"Actor {self.actor} turned ON")
                else:
                    logger.info(f"Actor {self.actor} is already ON, state unchanged.")
            else:  # Turn OFF
                if current_state:
                    await self.actor_off(self.actor)
                    logger.info(f"Actor {self.actor} turned OFF")
                else:
                    logger.info(f"Actor {self.actor} is already OFF, state unchanged.")
        
        # 2. Выполняем действие с AutoMode
        if self.automode_action == "Turn OFF":
            if self.AutoMode == True:
                await self.setAutoMode(False)
                logger.info("AutoMode turned OFF")
            else:
                logger.info("AutoMode was not active, nothing to turn off.")
        
        # 3. Уведомляем пользователя
        action_text = "turned ON" if self.action_type == "Turn ON" else "turned OFF"
        automode_text = "AutoMode turned OFF" if self.automode_action == "Turn OFF" else "AutoMode unchanged"
        delay_text = f" after {self.delay_minutes} min delay" if self.delay_minutes > 0 else ""
        
        self.cbpi.notify(
            self.name, 
            f'Target temp {self.kettle.target_temp}°C reached{delay_text}. Actor {action_text}. {automode_text}.', 
            NotificationType.SUCCESS
        )
        
        self.summary = "Action completed, moving to next step..."
        await self.push_update()
        
        # 4. Переход к следующему шагу
        await asyncio.sleep(1)
        await self.next()

    async def run(self):
        """Основной цикл: ждем температуру и запускаем задержку"""
        while self.running == True:
            await asyncio.sleep(1)
            
            # Защита от сбоев датчика
            sensor_data = self.get_sensor_value(self.sensor)
            if sensor_data and "value" in sensor_data:
                current_temp = float(sensor_data.get("value"))
                
                # Если температура достигнута И действие еще не запущено
                if current_temp >= self.kettle.target_temp and not self.temp_reached:
                    self.temp_reached = True
                    
                    if self.delay_timer is not None:
                        # Задержка есть: запускаем таймер
                        self.summary = f"Target reached. Starting {self.delay_minutes} min delay..."
                        await self.push_update()
                        logger.info(f"Target temp reached. Starting {self.delay_minutes} min delay.")
                        self.delay_timer.start()
                        self.delay_timer.is_running = True
                    else:
                        # Задержки нет: выполняем действие сразу
                        logger.info("Target temp reached. Executing action immediately.")
                        await self.execute_action()
                        return StepResult.DONE
                    
            await self.push_update()

        return StepResult.DONE

    async def on_stop(self):
        """Действие при РУЧНОЙ остановке шага (АВАРИЙНАЯ БЕЗОПАСНОСТЬ)"""
        self.summary = ""
        
        # 1. Останавливаем таймер задержки, если он идет
        if self.delay_timer is not None:
            await self.delay_timer.stop()
            
        # 2. Сбрасываем системную температуру и выключаем AutoMode
        if self.kettle is not None:
            self.kettle.target_temp = 0
        if self.AutoMode == True:
            await self.setAutoMode(False)
            
        # 3. ПРИНУДИТЕЛЬНО ВЫКЛЮЧАЕМ АКТОР при ручной остановке
        if self.actor is not None:
            await self.actor_off(self.actor)
            logger.info("Actor forced OFF due to manual step stop")
            
        logger.info("Step manually stopped. All equipment halted.")
        await self.push_update()

    async def setAutoMode(self, auto_state):
        """Управление встроенной логикой котла (PID/гистерезис)"""
        try:
            if (self.kettle.instance is None or self.kettle.instance.state == False) and (auto_state is True):
                url = "http://127.0.0.1:" + self.port + "/kettle/" + self.kettle.id + "/toggle"
                async with aiohttp.ClientSession() as session:
                    async with session.post(url) as response:
                        return await response.text()
            elif (self.kettle.instance.state == True) and (auto_state is False):
                await self.kettle.instance.stop()
                await self.push_update()
        except Exception as e:
            logging.error("Failed to switch KettleLogic {} {}".format(self.kettle.id, e))


def setup(cbpi):
    '''
    Регистрация плагина в системе CBPI4
    '''
    cbpi.plugin.register("ActorTempStep", ActorTempStep)