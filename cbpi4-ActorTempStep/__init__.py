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
    Property.Sensor(label="Sensor", description="Sensor to monitor. NOTE: If AutoMode=Yes, this MUST be the Kettle's sensor."),
    Property.Select(label="AutoMode", options=["Yes", "No"], description="Enable Kettle's PID/hysteresis at step start"),
    Property.Number(label="Temp", description="Target temperature (°C). Decimals supported (e.g. 76.2)", configurable=True),
    Property.Number(label="Delay", description="Delay in minutes before executing action (0 = no delay)", configurable=True),
    Property.Actor(label="Actor", description="Actor to control when target temp is reached"),
    Property.Select(label="Action", options=["Turn ON", "Turn OFF"], description="Action to perform when target temp is reached")
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
        self.step_completed_normally = False
        
        if self.kettle is not None:
            self.kettle.target_temp = float(self.props.get("Temp", 0.0))
            
        if self.delay_seconds > 0:
            self.delay_timer = Timer(self.delay_seconds, on_update=self.on_delay_timer_update, on_done=self.on_delay_timer_done)
        else:
            self.delay_timer = None
            
        if self.AutoMode == True:
            await self.setAutoMode(True)
            logger.info("AutoMode activated at step start")
            
        self.summary = f"Waiting for {self.kettle.target_temp:.1f}°C..." if self.kettle is not None else f"Waiting for {float(self.props.get('Temp', 0.0)):.1f}°C..."
        await self.push_update()

    async def on_delay_timer_update(self, timer, seconds):
        if self.delay_minutes > 0:
            self.summary = f"Delay: {Timer.format_time(seconds)}"
            await self.push_update()

    async def on_delay_timer_done(self, timer):
        await self.execute_action()

    async def execute_action(self):
        # Управление актором
        if self.actor is not None:
            current_state = getattr(self.actor, 'state', False)
            
            if self.action_type == "Turn ON":
                if not current_state:
                    await self.actor_on(self.actor)
                    logger.info(f"Actor {self.actor} turned ON at {self.kettle.target_temp:.1f}°C")
                else:
                    logger.info(f"Actor {self.actor} is already ON, state unchanged.")
            else:
                if current_state:
                    await self.actor_off(self.actor)
                    logger.info(f"Actor {self.actor} turned OFF at {self.kettle.target_temp:.1f}°C")
                else:
                    logger.info(f"Actor {self.actor} is already OFF, state unchanged.")
        
        # Уведомление
        action_text = "turned ON" if self.action_type == "Turn ON" else "turned OFF"
        delay_text = f" after {self.delay_minutes} min delay" if self.delay_minutes > 0 else ""
        
        self.cbpi.notify(
            self.name, 
            f'Target temp {self.kettle.target_temp:.1f}°C reached{delay_text}. Actor {action_text}.', 
            NotificationType.SUCCESS
        )
        
        self.summary = "Action completed, moving to next step..."
        await self.push_update()
        
        self.step_completed_normally = True
        
        # ВАЖНО: При нормальном завершении шага АВТОМАТИЧЕСКИ выключаем AutoMode
        # Если следующий шаг нужен нагрев — он сам включит AutoMode: Yes
        # Это обеспечивает безопасность: если рецепта больше нет, нагрев не останется включённым
        if self.AutoMode == True:
            await self.setAutoMode(False)
            logger.info("AutoMode turned OFF at step completion (safety)")
        
        await asyncio.sleep(1)
        await self.next()

    async def run(self):
        while self.running == True:
            await asyncio.sleep(1)
            
            sensor_data = self.get_sensor_value(self.sensor)
            if sensor_data and "value" in sensor_data:
                current_temp = float(sensor_data.get("value"))
                
                if current_temp >= self.kettle.target_temp and not self.temp_reached:
                    self.temp_reached = True
                    
                    if self.delay_timer is not None:
                        self.summary = f"Target reached. Starting {self.delay_minutes} min delay..."
                        await self.push_update()
                        logger.info(f"Target temp reached. Starting {self.delay_minutes} min delay.")
                        
                        self.delay_timer.start()
                        self.delay_timer.is_running = True
                    else:
                        logger.info("Target temp reached. Executing action immediately.")
                        await self.execute_action()
                        return StepResult.DONE
                    
            await self.push_update()

        return StepResult.DONE

    async def on_stop(self):
        self.summary = ""
        
        if self.delay_timer is not None:
            await self.delay_timer.stop()
            
        if self.kettle is not None:
            self.kettle.target_temp = 0
        if self.AutoMode == True:
            await self.setAutoMode(False)
            
        # При ручной остановке (STOP) выключаем ВСЁ для безопасности
        if self.actor is not None and not self.step_completed_normally:
            await self.actor_off(self.actor)
            logger.info("Actor forced OFF due to manual step stop")
            
        logger.info("Step stopped. All equipment halted.")
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
