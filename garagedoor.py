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
  the garage door or gate.
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
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue

from yombo.core.log import get_logger
from yombo.core.module import YomboModule
from yombo.utils.maxdict import MaxDict

logger = get_logger('modules.garagedoor')


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

        # Map of pending message for a given garageDeviceUUID.
        # key = garagedeviceuuid
        # Contains the orig message and msgUUID for pending command
        self.control_requests_pending = MaxDict(300)

        # used to store timers for pending/failed messages
        self.pendingTimers = MaxDict(300)

        self.garageCommands = (self._Commands['open'].command_id, self._Commands['close'].command_id)

    @inlineCallbacks
    def _load_(self):
        yield self._reload_()

    @inlineCallbacks
    def _reload_(self):
        """
        First, get a list of all devices we manage. validate that the commands
        we were given are valid for that device.
        """
        my_devices = self._devices()
        for device in my_devices:
            device_variables = yield device.device_variables()
            if device.validate_command(device_variables['controlpulsestart']['values'][0]) == False:
                logger.warn("Unable to control garage door '{device}, pulse start command is mising: {controlpulsestart}",
                            device=device.label,
                            controlpulsestart=device_variables['controlpulsestart']['values'][0] )
                continue
            if device.validate_command(device_variables['controlpulseend']['values'][0]) == False:
                logger.warn("Unable to control garage door '{device}, pulse end command is mising: {controlpulseend}",
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
                logger.warn("Invalid auto close time (length) is not a number.")
                continue

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
                'controlDevice': device_variables['controldevice']['values'][0],
                'controlPulseTime': controlPulseTime,
                'controlPulseStart': device_variables['controlpulsestart']['values'][0],
                'controlPulseEnd': device_variables['controlpulseend']['values'][0],
                'autoCloseTime': autoCloseTime,
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

            logger.debug("garage_data: %s" % garage_data)

    def _start_(self):
        """
        Sync the status of our virtual garage door (VGD) device to the input
        status device. These should always match.
        """
        for garage_id, garage in self.garageDevices.iteritems():
            self.set_garage_door_status(garage_id)

    def set_garage_door_status(self, device_id):

        def local_set_status(device, **kwargs):
            device.set_status(**kwargs)

        garage = self.garageDevices[device_id]
        garage_device = garage['device']
        garage_state = garage['device'].status_history[0]['machine_status']
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

        if closed_device.status_history[0]['machine_status'] == closed_state:
            if garage_state != closed_state:
                local_set_status(garage_state['device'],
                                 machine_status=closed_device.status_history[0]['machine_status'],
                                 machine_status_extra=closed_device.status_history[0]['machine_status_extra'],
                                 source=self._FullName)
                return closed_state

        if open_device.status_history[0]['machine_status'] == open_state:
            if garage_state != open_state:
                local_set_status(garage_state['device'],
                                 machine_status=open_device.status_history[0]['machine_status'],
                                 machine_status_extra=open_device.status_history[0]['machine_status_extra'],
                                 source=self._FullName)
                return open_state

        local_set_status(garage_state['device'],
                         machine_status=.5,
                         source=self._FullName)

        return machine_status

    def _stop_(self):
        """
        Stop sending messages.  Other components are unable to receive
        messages.  Queue up or pause functionality.
        """
        pass

    def _unload_(self):
        """
        Called just before the gateway is about to shutdown
        or reload all the modules.  Should assume gateway is going down.
        """
        pass

    def _set_status(self, device, command):
        if command.machine_label == "open":
            machine_status = 1
        else:
            machine_status = 0
        device.set_status(human_status=command.label,
                          human_message="The garage is: %s" % command.label,
                          machine_status=machine_status,
                          command=command)

    def _device_command_(self, **kwargs):
        """
        Implements the system hook to process garage door commands.
        """
        device = kwargs['device']

        if device.device_id not in self.garageDevices:
            logger.debug("Garage door module cannot handle device_type_id: {device_type_id}", device_type_id=device.device_type_id)
            return None

        print "garagedoor...device_command: %s" % device.label

        request_id = kwargs['request_id']

        command = kwargs['command']
        self.received_commands[request_id] = {
            'request_id': request_id,
            'callback': self.device_command_done,
            'device': device,
            'command': command,
        }

        if device.device_id in self.control_requests_pending:
            # If garage change is already pending, say sorry, can't now.
            logger.info("Garage door already has a pending request: {request}", request=self.control_requests_pending[request_id])
            device.device_command_failed(request_id, message="Pending already in progress.")
            return None

        # If garage already in requested position, let them know i'm lazy.
        garage_info = self.garageDevices[device.device_id]
        if garage_info['closedDevice'].status_history[0].machine_label != command.machine_label:
            logger.info("Looks like the input device status is not in the requested state. Will control garage door.")
            control_device = garage_info['controlDevice']
            control_device.command(garage_info['controlPulseStart'])
            control_device.command(garage_info['controlPulseEnd'], delay=garage_info['controlPulseTime']*.001)
            device.device_command_pending(request_id, message="Moving garage ")
        else:
            logger.info("Garage already in requested state. We will update our status and fake it.")
            device.device_command_done(request_id, message="Garage door already in requested position.")
            self._set_status(device, command)

    @inlineCallbacks
    def _device_status_(self, **kwargs):
        input_device = kwargs['device']
        status_type = None
        if input_device.device_id in self.garageClosedDevices:
            logger.info("Received status for a garage closed input device...")
            status_type = 'closed'
            garage_data = self.garageDevices[self.garageClosedDevices[input_device.device_id]]
            if garage_data['openDevice'] is None:
                if input_device.status_history[0]['human_status'] == garage_data['closedStateClosed']:
                    logger.info("garage door is now closed...  lets update some status and check if pending")
                    if garage_data['device'].device_id in self.control_requests_pending:
                        logger.info("We have a pending request... we will clear it out")
                if input_device.status_history[0]['human_status'] == garage_data['closedStateOpened']:
                    logger.info("garage door is now closed...  lets update some status and check if pending")
                    if garage_data['device'].device_id in self.control_requests_pending:
                        logger.info("We have a pending request... we will clear it out")


    def message(self, message):
        """
        The bulk of the work is done here.

        If status message, handle that first and get out of our hair.

        First, check if garage door is already in requested position. If so,
        then reply that it's already open/closed.

        Then, check if existing commands are pending for a given garage door.
        If so, tell the requestor a previous operation is pending results.

        Finally, the garage needs to be moved. Save the message for later,
        do a momentary relay command.  Then wait for input to change.  If it
        doesn't, then let the sender know the operation failed.
        """
        # Perhaps a status update from our input sensor. Check, and update
        # out garage door virutal device. Also, if any pending requests are
        # around, let them know.
        #logger.info("Garage got msg: %s" % message.dump())
        if message.msgType == 'status' and message['payload']['deviceobj'].device_id in self.garageInputDevices:
            # Get our VGD, and then set it's status.
            garageDeviceUUID = self.garageInputDevices[message['payload']['deviceobj'].device_id]
            garageDevice = self.garageDevices[garageDeviceUUID]['device']
            garageDevice.set_status(status=message.payload['status'].status, statusExtra=message.payload['status'].statusextra, source=self._FullName)

            # Now see if we have pending items for this garageDevice. If we do,
            # send a response. We'll also need to delete requests forwarded
            # and stop/delete any pending timers.
            if garageDeviceUUID in self.controlRequestsPending:
                origMsg = self.controlRequestsPending[garageDeviceUUID]['message']

#                textStatus = ''
                if message.payload['status'].status == 'open': #opening...
                    textStatus = "%s is now open." % garageDevice.label
                else:
                    textStatus = "%s is now closed." % garageDevice.label

                replyMsg = origMsg.getReply(msgStatus="done", msgStatusExtra=message.payload['status'], textStatus = textStatus)
                replyMsg.send()

                # now delete pending/failed timer, and cleanup pending dictionaries
                self.deleteTimer(self.controlRequestsPending[garageDevice.device_id]['messageOrig'])
                if self.controlRequestsPending[garageDevice.device_id]['messageOrig'] in self.requestsMessagesOrig:
                    del self.requestsMessagesOrig[self.controlRequestsPending[garageDevice.device_id]['messageOrig']]
                del self.controlRequestsPending[garageDevice.device_id]
            return


        #From here on, we only care about message to us if it's not a status update.
        if message.msgDestination != self._FullName.lower():
            logger.debug("Discarding rogue message - don't worry.")
            return

        # toss out messages that have a bad command for us.
        logger.info("garage commands: %s " % str(self.garageCommands))
        logger.info("payload  : %s" % message.dump())
        if 'cmdobj' in message.payload and message.payload['cmdobj'].cmdUUID not in self.garageCommands:
            logger.warn("Discarding garage message, we don't know what that command does.")
            return

        # toss out messages that have a bad command for us.
        if 'cmdobj' in message.payload and message.payload['deviceobj'].device_id not in self.garageDevices:
            logger.warn("Unable to control a garage door not assigned to this module. Sorry.")
            return

        # lets process cmd returns.  Treat returns as only status updates.
        # Don't send that it's really done until input senor tells us!
        logger.info("aaaa")
        if message.msgType == 'cmd' and message.msgStatus != 'new':
          logger.info("bbbb")
          if message.msgOrigUUID in self.requestsMessagesOrig:
            logger.info("ccc")
            origMsg = self.requestsMessagesOrig[message.msgOrigUUID]['message']
            if (message.msgStatus == 'done' or message.msgStatus == 'processing'):
                theArgs = {'msgStatus' : 'processing'}
                if (origMsg.payload['cmdUUID'] == 'dhPt1CzzjDjEfTddtpiJSKKe'): #opening...
                  theArgs['msgStatusExtra'] = "opening garage"
                  theArgs['textStatus'] = "Processing request to open garage."
                elif (origMsg.payload['cmdUUID'] == 'x8mXp46JIXkEdYFZ5lUEtw1L'): #closing...
                  theArgs['msgStatusExtra'] = "closing garage"
                  theArgs['textStatus'] = "Processing request to close garage."

                # we only send that it's in progress because we wait to input
                # sensor to tell us the truth.
                self.sendProcessing(message.msgOrigUUID, **theArgs) #original being request sent to device module

            # Something has happened.  Lets give up and tell the user.
            else:
                if message.msgOrigUUID in self.pendingTimers and callable(self.pendingTimers[message.msgOrigUUID].cancel):
                    if self.pendingTimers[message.msgOrigUUID].active():
                        self.pendingTimers[message.msgOrigUUID].cancel()
                        del self.pendingTimers[message.msgOrigUUID]
                reply = origMsg.getReply(**message.dump())
                reply.send()

                # don't forget to cleanup messages and timers that are pending
                self.deleteTimer(message.msgOrigUUID)
                if self.requestsMessagesOrig[message.msgOrigUUID]['garageDeviceUUID'] in self.controlRequestsPending:
                    del self.controlRequestsPending[ self.requestsMessagesOrig[message.msgOrigUUID]['garageDeviceUUID'] ]
                del self.requestsMessagesOrig[message.msgOrigUUID]
            return

        # We FINALLY get to process new commands! YAY!
        if message.msgType == 'cmd' and message.msgStatus == 'new':
            logger.info("zzzz")

            garageDeviceUUID = message['payload']['deviceobj'].device_id
            garageDevice = self.garageDevices[garageDeviceUUID]['device']

            # If garage change is already pending, say sorry, can't now.
            if garageDevice.device_id in self.controlRequestsPending:
                logger.info("xxxx")
                reply = message.getReply(msgStatus="failed", msgStatusExtra="A request for this device is currently pending. Try again later." )
                reply.send
                return

            # If garage already in requested position, let them know i'm lazy.
            if message['payload']['cmdobj'].cmd == garageDevice.status[0].status:
                logger.info("yyyy")
                action = 'closed'
                label = garageDevice.label
                if garageDevice.status[0].status == 'open':
                  action = 'open'
                output = "%s already %s, nothing to do." % (garageDevice.label, action)
                replyMsg = message.getReply(msgStatus="done",
                  msgStatusExtra=output,
                  textStatus=output)
                replyMsg.send()
                return

            # If we made it this  far, the garage needs to be moved.
            # First, start the relay with pulseStart command, then stop
            # with pulseStop
            controlDevice = self.garageDevices[garageDeviceUUID]['controlDevice']
            ctlmsg = controlDevice.get_message(self, cmd=self.garageDevices[garageDeviceUUID]['controlPulseStart'])

            # Undo the relay action.
            ctlmsg2 = controlDevice.get_message(self, cmd=self.garageDevices[garageDeviceUUID]['controlPulseEnd'])

            self.controlRequestsPending[garageDevice.device_id] = {
                'message' : message,
                'messageOrig' : ctlmsg2.msgUUID,
            }
            self.requestsMessagesOrig[ctlmsg2.msgUUID] = {
                'message' : message,
                'garageDeviceUUID' : garageDevice.device_id,
            }

            # now we send the actual commands now that everything is setup.
            ctlmsg.send()

            #time entered is in milliseconds. Convert an int to milliseconds.
            reactor.callLater(self.garageDevices[garageDeviceUUID]['controlPulseTime']*.001, ctlmsg2.send)

            # This sends a "processing" message incase we don't get something
            # back soon. This is typical as garages take a while to close.
            self.pendingTimers[ctlmsg2.msgUUID] = reactor.callLater(0.99, self.sendProcessing, ctlmsg2.msgUUID)

            # catchall to cleanup anything left over...
            reactor.callLater(1200, self.cleanUpcontrolRequestsPending, garageDevice.device_id)
            reactor.callLater(1200, self.cleanUprequestsMessagesOrig, ctlmsg2.msgUUID)

    def sendProcessing(self, msgUUID, **kwargs):
        origMsg = self.requestsMessagesOrig[msgUUID]['message']
        reply = origMsg.getReply(msgStatus=kwargs.get('msgStatus',"processing"), msgStatusExtra=kwargs.get('msgStatusExtra', "Command sent to garage door controller, pending results.") )
        reply.send()

        self.deleteTimer(msgUUID)

        self.pendingTimers[msgUUID] = reactor.callLater(60, self.sendFailed, msgUUID)

    def sendFailed(self, msgUUID, **kwargs):
        origMsg = self.requestsMessagesOrig[msgUUID]['message']
        logger.info("send failed?? %s" % origMsg.dump())
        device_id = origMsg['payload']['deviceobj'].device_id

        if self.requestsMessagesOrig[msgUUID]['garageDeviceUUID'] in self.controlRequestsPending:
            del self.controlRequestsPending[self.requestsMessagesOrig[msgUUID]['garageDeviceUUID']]
        del self.requestsMessagesOrig[msgUUID]
        
        self.deleteTimer(msgUUID)

        reply = origMsg.getReply(msgStatus=kwargs.get('msgStatus',"failed"), msgStatusExtra=kwargs.get('msgStatusExtra', "Command sent to garage door controller, however, garage never reported a status change.") )
        reply.send()
        
    def deleteTimer(self, timerid):
        if timerid in self.pendingTimers and callable(self.pendingTimers[timerid].cancel):
            if self.pendingTimers[timerid].active():
                self.pendingTimers[timerid].cancel()
                del self.pendingTimers[timerid]
                
    def cleanUpcontrolRequestsPending(self, itemID):
        if itemID in self.controlRequestsPending:
          del self.controlRequestsPending[itemID]

    def cleanUprequestsMessagesOrig(self, itemID):
        if itemID in self.requestsMessagesOrig:
          del self.requestsMessagesOrig[itemID]
	
