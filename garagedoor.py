"""
This module can control a garage door, gate, or any device that requires a
momentary relay close or open. It requires at least one input sensor at the closed
position to note when the garage door, gate, etc is closed. Optionally, another
input sensor can be used to ensure the device is in the proper input position.

Three additional input sensors can be used:

# Motion detector: The motion detector should close (makes the input go high) the
  circuit when motion is detected. This will reset the automatic close timer. For
  gates, this could be a car occupancy sensor underground.
# Autoclose disable: A switch that closes a circuit (makes the input go high) when
  the user wishes to temporarily disable the automatic closure of the device. This
  can be an occupany sensor and/or switch (wired in series) which would disable the
  automation close function.
# Autoclose alarm: A device that can act as an auto close alarm to alert anyone near
  the garage door or gate before it's going to be automatically closed.
# Disable control: A switch that closes a circuit (makes the input go high) when
  the user wishes to disable this module from controlling this garage door.

This module enabled a new device type "Garage Door" and is considered a virtual
device. It uses another device to pulse a relay which acts like someone pressed
the garage door button manually.

The virtual garage device (VGD) status mirrors the closed input device status. If 
the input device is closed, then the VGD status is closed.

When the user sends and open or close command to the VGD, it first checks to
see if the garage is in the desired position already. If it is, it will respond
with "done". If not, it will send two commands to the control device. First,
it will send a close and then an open.

This module is considered complex because it has to track if a command to is
is already pending, as well as checking various input devices for decision logic.

:copyright: 2012-2017 Yombo
:license: YRPL 1.6
"""
import traceback

from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue

from yombo.core.log import get_logger
from yombo.core.module import YomboModule
from yombo.utils.maxdict import MaxDict

logger = get_logger('modules.garagedoor')

COMMAND_STATUS = {
    'open': 1,
    'close': 0,
}

class GarageDoor(YomboModule):
    """
    Empty base module

    :cvar garageInput: The device id to monitor for garage door status.
    :cvar garageControl: The device id to pulse to control a garage door.
    :cvar deviceType: The device_type_id that is a garage door device.
    """
    def _init_(self):
        """
        Define some basic start up items.
        """
#        self._RegisterVoiceCommands = [
#          {'voice_cmd': "master garage door [close all, open all]", 'order' : 'nounverb'}
#          ]

        self.received_commands = MaxDict(300) # stores incoming commands for status update when done.

        self.garageDevices = {} # mapping of Virtual Garage Doors to real garage controllers.
        # garageDevices[VGD] = { real device and control information }
        
        self.garageClosedDevices = {} # mapping of Virtual Garage Doors to input sensors
        self.garageOpenDevices = {} # mapping of Virtual Garage Doors to input sensors

        # Map of pending control requests. Key is the target garage door id with a value to request_id.
        self.control_requests_pending = {}

        # used to store timers for pending/failed messages
        self.garageCommands = (self._Commands['open'].command_id, self._Commands['close'].command_id)

    @inlineCallbacks
    def _load_(self):
        yield self._reload_()

    def _reload_(self):
        """
        First, get a list of all devices we manage. validate that the commands
        we were given are valid for that device.
        """
        my_devices = self._devices()
        for device_id, device in my_devices.iteritems():
            device_variables = device.device_variables

            try:
                controlDevice_id = device_variables['controldevice']['values'][0]
                # print "controlDevice_id: %s" % controlDevice_id
                controlDevice = self._Devices[controlDevice_id]
                # print "controlDevice::available commands:: %s" % controlDevice.available_commands()
                # print "controldevice: %s" % controlDevice.__dict__
            except Exception as e:
                logger.warn("Unable to control garage door '{device}', invalid control device '{controlDevice}': {e}",
                            device=device.label, e=e)
                continue

            if controlDevice.validate_command(device_variables['controlpulsestart']['values'][0]) == False:
                logger.warn("Unable to control garage door '{device}, pulse start command is mising from control device: {controlpulsestart}",
                            device=device.label,
                            controlpulsestart=device_variables['controlpulsestart']['values'][0] )
                continue
            if controlDevice.validate_command(device_variables['controlpulseend']['values'][0]) == False:
                logger.warn("Unable to control garage door '{device}, pulse end command is mising from control device: {controlpulseend}",
                            device=device.label,
                            controlpulseend=device_variables['controlpulseend']['values'][0] )
                continue
            try:
                controlPulseTime = float(device_variables['controlpulsetime']['values'][0])
            except:
                logger.warn("Invalid control pulse time (length) is not a number.")
                continue

            if controlPulseTime <= 0:
                logger.warn("Unable to control garage door '{device}, pulse time too short: {controlPulseTime}",
                            device=device.label,
                            controlPulseTime=controlPulseTime)
                continue

            try:
                autoCloseTime = float(device_variables['autoclosetime']['values'][0])
            except:
                autoCloseTime = 0
                logger.warn("Invalid auto close time (length) is not a number. Disabling autoclose.")

            try:
                closeTimeout = float(device_variables['closetimeout']['values'][0])
            except:
                closeTimeout = 5
                logger.warn("Invalid close timeout (length) is not a number. Setting to 45 seconds.")

            try:
                openTimeout = float(device_variables['closetimeout']['values'][0])
            except:
                openTimeout = 5
                logger.warn("Invalid open timeout (length) is not a number. Setting to 45 seconds.")

            try:
                closed_device = self._Devices[device_variables['closeddevice']['values'][0]]
            except:
                logger.warn("Unable to control garage door '{device}, closed device missing: {closeddevice}",
                            device=device.label,
                            closeddevice=device_variables['closeddevice']['values'][0] )
                continue
            garage_data = {
                'device' : device,
                'closedDevice' : closed_device,
                'closedStateClosed': device_variables['closedstateclosed']['values'][0],
                'closedStateOpened': device_variables['closedstateopened']['values'][0],
                'controlDevice': controlDevice,
                'controlPulseTime': controlPulseTime,
                'controlPulseStart': device_variables['controlpulsestart']['values'][0],
                'controlPulseEnd': device_variables['controlpulseend']['values'][0],
                'autoCloseTime': autoCloseTime,
                'closeTimeout': closeTimeout,
                'openTimeout': openTimeout,
            }

            try:
                garage_data['openDevice'] = self._Devices[device_variables['opendevice']['values'][0]]
                garage_data['openStateOpened'] = device_variables['openstateopened']['values'][0]
                self.garageOpenedDevices[garage_data['opendevice'].device_id] = device.device_id
            except:
                logger.warn("No open device found. Will assume garage door ({device}) is open if it's not closed.",
                            device=device.label)
                garage_data['openDevice'] = None

            try:
                garage_data['autoCloseDisableDevice'] = self._Devices[device_variables['autoCloseDisableDevice']['values'][0]]
                garage_data['autoCloseDisableDeviceEnabledState'] = self._Devices[device_variables['autoCloseDisableDeviceEnabledState']['values'][0]]
                garage_data['autoCloseDisableDeviceDisabledState'] = self._Devices[device_variables['autoCloseDisableDeviceDisabledState']['values'][0]]
            except:
                logger.error("No auto close disable device found or it's states are invalid. No auto close override will be available.",
                            device=device.label)
                garage_data['autoCloseDisableDevice'] = None

            try:
                garage_data['autoCloseAlertDevice'] = self._Devices[device_variables['autoCloseAlertDevice']['values'][0]]
                garage_data['autoCloseAlertStartCommand'] = self._Devices[device_variables['autoCloseAlertStartCommand']['values'][0]]
                garage_data['autoCloseAlertEndCommand'] = self._Devices[device_variables['autoCloseAlertEndCommand']['values'][0]]
            except:
                logger.error("No auto close alert device found, or alert times are invalid. No auto close alert will be available.",
                            device=device.label)
                garage_data['autoCloseAlertDevice'] = None

            if garage_data['autoCloseAlertDevice'] is not None:
                try:
                    garage_data['autoCloseAlertBeforeTime'] = int(self._Devices[device_variables['autoCloseAlertBeforeTime']['values'][0]])
                except:
                    logger.warn("Auto close alert before time is invalid. Setting to 30 seconds.",
                                device=device.label)
                    garage_data['autoCloseAlertBeforeTime'] = 30

                try:
                    garage_data['autoCloseAlertAfterTime'] = int(self._Devices[device_variables['autoCloseAlertAfterTime']['values'][0]])
                except:
                    logger.warn("Auto close alert after time is invalid. Setting to 10 seconds.",
                                device=device.label)
                    garage_data['autoCloseAlertAfterTime'] = 10

            self.garageDevices[device.device_id] = garage_data
            self.garageClosedDevices[closed_device.device_id] = device.device_id

            # logger.info("garage_data: %s" % garage_data)

    def _start_(self):
        """
        Sync the status of our virtual garage door (VGD) device to the input
        status device. These should always match.
        """
        for garage_id, garage in self.garageDevices.iteritems():
            self.set_garage_door_status(garage_id)

    def get_garage_door_status(self, device_id):

        if device_id not in self.garageDevices:
            raise YomboModule("Device ID not in list of available garages.")

        garage = self.garageDevices[device_id]
        garage_device = garage['device']
        garage_state = garage['device'].status_history[0].machine_status
        machine_status = None
        machine_status_extra = None

        if garage['openDevice'] is not None:  #we have a sensor for open and closed. Device is neither of these, machine status = .5
            closed_device =  garage['closedDevice']
            open_device =  garage['openDevice']
            closed_state =  garage['closedStateClosed']
            open_state =  garage['openStateOpened']
        else:
            closed_device =  garage['closedDevice']
            open_device =  garage['closedDevice']
            closed_state =  garage['closedStateClosed']
            open_state =  garage['closedStateOpened']

        # print "closed_device.status_history[0].machine_status: %s" % closed_device.status_history[0].machine_status
        # print "open_device.status_history[0].machine_status: %s" % closed_device.status_history[0].machine_status
        # print "closed_state: %s" % closed_state
        # print "open_state: %s" % open_state
        if closed_device.status_history[0].machine_status == float(closed_state):
            if garage_state != closed_state:
                return {
                    'human_status': "Closed",
                    'machine_status': 0,
                }

        if open_device.status_history[0].machine_status == float(open_state):
            if garage_state != open_state:
                return {
                    'human_status': "Open",
                    'machine_status': 1,
                }

        return {
            'human_status': "Unknown",
            'machine_status': .5,
        }

    def set_garage_door_status(self, device_id):

        def local_set_status(device, **kwargs):
            kwargs['human_message'] = "Garage Door is %s" % kwargs['human_status'].lower()
            kwargs['reported_by'] = 'yombo.lib.modules.GarageDoor'
            device.set_status(**kwargs)

        if device_id not in self.garageDevices:
            return

        status = self.get_garage_door_status(device_id)
        status['reported_by'] = self._FullName
        local_set_status(self.garageDevices[device_id]['device'], **status)

        return status['machine_status']


    def _set_status(self, device, command):
        if command.machine_label == "open":
            machine_status = 1
        else:
            machine_status = 0
        device.set_status(human_status=command.label,
                          human_message="The garage is: %s" % command.label,
                          machine_status=machine_status,
                          command=command)

    def device_command_done(self, **kwargs):
        """
        
        :param kwargs: 
        :return: 
        """
        pass

    def _device_command_(self, **kwargs):
        """
        Implements the system hook to process garage door commands.
        """
        try:
            device = kwargs['device']

            # print "garagedoor...garageDevices: %s" % self.garageDevices

            if device.device_id not in self.garageDevices:
                logger.debug("Garage door module cannot handle device_type_id: {device_type_id}", device_type_id=device.device_type_id)
                return None

            # print "garagedoor...device_command: %s" % device.label

            request_id = kwargs['request_id']

            command = kwargs['command']
            self.received_commands[request_id] = {
                'request_id': request_id,
                'call_later': None,
                'device': device,
                'command': command,
            }

            if device.device_id in self.control_requests_pending:
                # If garage change is already pending, say sorry, can't now.
                logger.info("Garage door already has a pending request: {request}", request=self.control_requests_pending[device.device_id])
                device.device_command_failed(request_id, message="Pending already in progress.")
                return None

            # If garage already in requested position, let them know i'm lazy.
            garage_info = self.garageDevices[device.device_id]
            current_status = self.get_garage_door_status(device.device_id)

            if current_status['machine_status'] != COMMAND_STATUS[command.machine_label]:
                timeout_label = command.machine_label + "Timeout"
                logger.info("Looks like the input device status is not in the requested state. Will control garage door.")
                self.control_requests_pending[device.device_id] = request_id
                control_device = garage_info['controlDevice']
                control_device.command(garage_info['controlPulseStart'])
                control_device.command(garage_info['controlPulseEnd'], delay=garage_info['controlPulseTime']*.001, max_delay=999999)
                self.received_commands[request_id]['call_later'] = reactor.callLater(garage_info[timeout_label],
                                                                                     self.garage_door_timed_out,
                                                                                     request_id)
                device.device_command_pending(request_id, message="Moving garage ")
            else:
                logger.info("Garage already in requested state. We will update our status and fake it.")
                device.device_command_done(request_id, message="Garage door already in requested position.")
                self.set_garage_door_status(device.device_id)
        except Exception as e:  # exceptions getting swalled
            logger.info("Exception found: {e}", e=e)
            logger.info("---------------==(Traceback)==--------------------------")
            logger.info("{trace}", trace=traceback.format_exc())
            logger.info("--------------------------------------------------------")


    def _device_status_(self, **kwargs):
        input_device = kwargs['device']
        if input_device.device_id in self.garageClosedDevices:
            logger.info("Received status for a garage close input device...")
            garage_data = self.garageDevices[self.garageClosedDevices[input_device.device_id]]
            self.set_garage_door_status(garage_data['device'].device_id)
        elif input_device.device_id in self.garageOpenDevices:
            logger.info("Received status for a garage open input device...")
            garage_data = self.garageDevices[self.garageOpenDevices[input_device.device_id]]
            self.set_garage_door_status(garage_data['device'].device_id)
        else:
            # nothing here for us.. by bye.
            return

        device = garage_data['device']
        # print "checking if was a pending garage..or is in: %s" % self.control_requests_pending
        # print "device id: %s" % device.device_id
        # check and process if this is a result of a previous request
        if device.device_id in self.control_requests_pending:
            request_id = self.control_requests_pending[device.device_id]
            device = self.received_commands[request_id]['device']
            device.device_command_done(request_id, message='Done')
            try:
                garage_data['call_later'].cancel()
            except:  # might already be canceled or was called..
                pass
            del self.control_requests_pending[device.device_id]
            del self.received_commands[request_id]

    def garage_door_timed_out(self, request_id):
        if request_id in self.received_commands:
            device = self.received_commands[request_id]['device']
            logger.warn("Garage door never completed request: {command}",
                        command=self.received_commands[request_id]['command'].label)
            del self.received_commands[request_id]
            del self.control_requests_pending[device.device_id]
