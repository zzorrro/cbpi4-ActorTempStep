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
    Property.Sensor(label="Sensor", description="Sensor to monitor."),
    Property.Select(label="AutoMode", options=["Yes", "No"], description="Enable Kettle's PID/hysteresis at step start"),
    Property.Number(label="Temp", description="Target temperature (°C). Decimals supported.", configurable=True),
    Property.Number(label="Delay", description="Delay in minutes before executing action (0 = no delay)", configurable=True),
    Property.Actor(label="Actor", description="Actor to control when target temp is reached"),
    Property.Select(label="Action", options=["Turn ON", "Turn OFF"], description="Action to perform")
])
class ActorTempStep(CBPiStep):

    async def on_start(self):
        self.port = str(self.cbpi.static_config.get('port', 8000))
        self.kettle = self.get_kettle(self.props.Kettle)
        self.sensor = self.props.Sensor
        self.AutoMode = True if self.props.get("AutoMode", "No") == "Yes" else False
        self.actor = self.props.get("Actor", None)
        self.action_type = self.props.get("Action", "Turn ON")
        
        self.delay_minutes = int(self.props.get("Delay", 0))
        self.delay_seconds = self.delay_minutes * 60
        self.temp_reached = False
        
        if self.kettle is not None:
            self.kettle.target_temp = float(self.props.get("Temp", 0.0))
            
        if self.AutoMode == True:
            await self.setAutoMode(True)
            
        # Инициализируем таймер ТОЛЬКО если есть задержка
        if self.delay_seconds > 0:
            self.delay_timer = Timer(self.delay_seconds, on_update=self.on_delay_timer_update, on_done=self.on_delay_timer_done)
        else:
            self.delay_timer = None
            
        self.summary = f"Waiting for {self.kettle.target_temp:.1f}°C..." if self.kettle is not None else "Waiting..."
        await self.push_update()

    # Коллбэк обновления (работает автоматически)
    async def on_delay_timer_update(self, timer, seconds):
        self.summary = f"Delay: {Timer.format_time(seconds)}"
        await self.push_update()

    # Коллбэк завершения (работает автоматически, когда время вышло)
    async def on_delay_timer_done(self, timer):
        await self.execute_action()

    async def execute_action(self):
        if self.actor is not None:
            current_state = getattr(self.actor, 'state', False)
            if self.action_type == "Turn ON" and not current_state:
                await self.actor_on(self.actor)
                logger.info(f"Actor {self.actor} turned ON")
            elif self.action_type == "Turn OFF" and current_state:
                await self.actor_off(self.actor)
                logger.info(f"Actor {self.actor} turned OFF")
        
        action_text = "turned ON" if self.action_type == "Turn ON" else "turned OFF"
        delay_text = f" after {self.delay_minutes} min delay" if self.delay_minutes > 0 else ""
        
        self.cbpi.notify(self.name, f'Target temp {self.kettle.target_temp:.1f}°C reached{delay_text}. Actor {action_text}.', NotificationType.SUCCESS)
        
        if self.AutoMode == True:
            await self.setAutoMode(False)
            
        self.summary = "Action completed."
        await self.push_update()
        await asyncio.sleep(1)
        await self.next() # Переход к следующему шагу

    async def run(self):
        while self.running == True:
            await asyncio.sleep(1)
            sensor_data = self.get_sensor_value(self.sensor)
            
            if sensor_data and "value" in sensor_data:
                current_temp = float(sensor_data.get("value"))
                
                # Ждем температуру И проверяем, что таймер еще не запущен
                if current_temp >= self.kettle.target_temp and not self.temp_reached:
                    self.temp_reached = True
                    
                    if self.delay_timer is not None:
                        self.summary = f"Target reached. Starting {self.delay_minutes} min delay..."
                        await self.push_update()
                        # Запускаем таймер и доверяем ему вызвать on_delay_timer_done
                        self.delay_timer.start()
                    else:
                        # Если задержки нет, вызываем действие сразу
                        await self.execute_action()
                        return StepResult.DONE # Прерываем run, так как действие уже вызвало self.next()
                        
            await self.push_update()
        return StepResult.DONE

    async def on_stop(self):
        self.summary = ""
        if self.delay_timer is not None:
            await self.delay_timer.stop() # Останавливаем таймер при ручной остановке
            
        if self.kettle is not None:
            self.kettle.target_temp = 0
        if self.AutoMode == True:
            await self.setAutoMode(False)
            
        if self.actor is not None:
            await self.actor_off(self.actor)
            
        logger.info("Step stopped manually. All equipment halted.")
        await self.push_update()

    async def setAutoMode(self, auto_state):
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
    cbpi.plugin.register("ActorTempStep", ActorTempStep)
