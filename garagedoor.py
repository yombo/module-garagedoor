"""
This module can control a garage door. It uses a virtual device that users
setup when instaling this module. It acts as a point to control, but is not
a real device.

This module uses two real devices. One device is for getting the status of the
garage and is known as the the "input" device. This is typically a sensor such
as a magnetic reed switch or some other switch connected to an input device.

The second device is used to control the garage motor. This module expects to
momentarily activate the relay. This mimics what would happen if the user
pressed a button to open/close a garage. This is known as the "control" device.

The virtual garage device (VGD) status mirrors the input device status. If 
the input device is closed, then the VGD status is closed. 

When the user sends and open or close command to the VGD, it first checks to
see if the garage is in the desired position already. If it is, it will respond
with "done".  If not, it will send two commands to the control device. First,
it will send a close and then an open.

This module has to track lots of messages flying around. It has to track
messages it receives so it knows who to respond to when it's all done. It also
generates messages (commands) to the control device, and needs to track
those messages to.  It uses this tracking system to monitor the progress
of the garage door. If the garage door doesn't respond, it will reply to the
original sender of an error.  If the control device reports an error, it will
forward that error message on.

:copyright: 2012 Yombo
:license: RPL 1.5
"""
from twisted.internet.reactor import callLater

from yombo.core.helpers import getDevice, getDevicesByType
from yombo.core.log import getLogger
from yombo.core.module import YomboModule

logger = getLogger('modules.garagedoor')

class GarageDoor(YomboModule):
    """
    Empty base module

    :cvar garageInput: The device id to monitor for garage door status.
    :cvar garageControl: The device id to pulse to control a garage door.
    :cvar deviceType: The deviceTypeUUID that is a garage door device.
    """

    def init(self):
        """
        Define some basic start up items.
        """
        self._ModDescription = "Manages garage doors."
        self._ModAuthor = "Mitch Schwenk @ Yombo"
        self._ModUrl = "http://www.yombo.net"

        self.ourDeviceType = "JzHX3btaHD8FBLQFlav9solU"  # our virtual device type
        
        self.garageDevices = {} # mapping of VGD to real garage controllers.
        # garageDevices[VGD] = { real device and control information }
        
        self.garageInputDevices = {} # mapping of VGD to input sensors

        self.requestsForwarded = {} # messages we generated to control a garage
        self.pendingRequests = {} # 
        self.pendingTimers = {}

        self.garageCommands = ('EZbM3Z01vTsV72JoGMDiBHej', 'EZbCa3HcxQwGN1wXbYXcp7pv')
                                #  open                     close 

    def load(self):
        """
        First, get a list of all devices we manage. validate that the commands
        we were given are valid for that device. 
        Gets device information. Gets ready to handle commands.
        """
        for item in self._DevicesByType(self.ourDeviceType):
            if item.validateCommand(item.deviceVariables['controlPulseStart'][0]) == False:
                logger.warning("Invalid control pulse start command.")
                continue
            if item.validateCommand(item.deviceVariables['controlPulseEnd'][0]) == False:
                logger.warning("Invalid control pulse end command.")
                continue
            try:
                controlPulseTime = int(item.deviceVariables['controlPulseTime'][0])
            except:
                logger.warning("Invalid control pulse time (length) is not a number.")
                continue

            self.garageDevices[item.deviceUUID] = {
                'device' : item,
                'inputDevice' : getDevice(item.deviceVariables['inputDevice'][0]),
                'inputStateClosed' : item.deviceVariables['inputStateClosed'][0],
                'inputStateOpen' : item.deviceVariables['inputStateOpen'][0],
                'controlDevice' : getDevice(item.deviceVariables['controlDevice'][0]),
                'controlPulseTime' : controlPulseTime,
                'controlPulseStart' : item.deviceVariables['controlPulseStart'][0],
                'controlPulseEnd' : item.deviceVariables['controlPulseEnd'][0],
                }                                         

            self.garageInputDevices[item.deviceVariables['inputDevice'][0]] = item.deviceUUID

    def start(self):
        """
        Nothing to do... Yet...?
        """
        pass
    
    def stop(self):
        """
        Stop sending messages.  Other components are unable to receive
        messages.  Queue up or pause functionality.
        """
        pass
    
    def unload(self):
        """
        Called just before the gateway is about to shutdown
        or reload all the modules.  Should assume gateway is going down.
        """
        pass

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
        logger.info("Garage got msg: %s" % message.dump())
        if message.msgType == 'status' and message['payload']['deviceUUID'] in self.garageInputDevices:
            # Get our VGD, and then set it's status.
            garageDevice = self.garageDevices[message['payload']['deviceUUID']]['device']
            garageDevice.setStatus(status=message.payload['status'], statusExtended=message.payload.get('statusExtra', ''))
            if message['payload']['deviceUUID'] in self.pendingRequests:
                origMsg = self.requestsForwarded[message['payload']['deviceUUID']]
                replyMsg = origMsg.getReply(msgStatus="done", msgStatusExtra=message.payload['status'])
                replyMsg.send()
            return

        #We only care about message to us if it's not a status update.
        if message.msgDestination != self._FullName.lower():
            logger.warning("Discarding rogue message!!!")
            return  


        if message.msgOrigUUID in self.requestsForwarded:
            origMsg = self.requestsForwarded[message.msgOrigUUID]
            if (message.msgStatus == 'done' or message.msgStatus == 'processing'):
                theArgs = {'msgStatus' : 'processing'}
                if (origMsg.payload['cmdUUID'] == 'EZbM3Z01vTsV72JoGMDiBHej'): #opening...
                  theArgs['msgStatusExtra'] = "opening garage"
                elif (origMsg.payload['cmdUUID'] == 'EZbCa3HcxQwGN1wXbYXcp7pv'): #closing...
                  theArgs['msgStatusExtra'] = "opening garage"

                self.sendProcessing(message.msgOrigUUID, **theArgs) #original being request sent to device module
            else:
                if message.msgOrigUUID in self.pendingTimers and callable(self.pendingTimers[message.msgOrigUUID].cancel):
                    if self.pendingTimers[message.msgOrigUUID].active():
                        self.pendingTimers[message.msgOrigUUID].cancel()
                        del self.pendingTimers[message.msgOrigUUID]
                reply = origMsg.getReply(**message.dump())
                reply.send()

                del self.requestsForwarded[message.msgOrigUUID]
                del self.pendingRequests
                return

#        device = None
        deviceUUID = ''
        deviceTypeUUID = ''
        if message.msgType == 'cmd' and message.msgStatus == 'new' and message['payload']['cmdUUID'] in self.garageCommands:
            deviceUUID = message['payload']['deviceUUID']
            garageDevice = self.garageDevices[deviceUUID]['device']

            logger.info(self.pendingRequests)
            if garageDevice.deviceUUID in self.pendingRequests:
                reply = message.getReply(msgStatus="failed", msgStatusExtra="A request for this device is currently pending. Try again later." )
                reply.send
                return

            self.pendingRequests[garageDevice.deviceUUID] = message

            if message['payload']['cmd'] == garageDevice.status:
                replyMsg = message.getReply(msgStatus="done")
                replyMsg.send()
                return


            inputDevice = self.garageDevices[deviceUUID]['inputDevice']
            
            # if garage needs to be moved:
            controlDevice = self.garageDevices[deviceUUID]['controlDevice']
            ctlmsg = controlDevice.getMessage(self, cmdUUID=self.garageDevices[deviceUUID]['controlPulseStart'])

            # after delay, stop the control pulse
            ctlmsg2 = controlDevice.getMessage(self, cmdUUID=self.garageDevices[deviceUUID]['controlPulseEnd'])

            self.requestsForwarded[ctlmsg2.msgUUID] = message

            ctlmsg.send()

            #time entered is in milliseconds. Convert an int to milliseconds.
            callLater(self.garageDevices[deviceUUID]['controlPulseTime']*.001, ctlmsg2.send)

            self.pendingTimers[ctlmsg2.msgUUID] = callLater(1.1, self.sendProcessing, ctlmsg2.msgUUID)

            # catchall to cleanup anything left over...
            callLater(1200, self.cleanUpRequestsForwarded, ctlmsg2.msgUUID)
            callLater(1200, self.cleanUpPendingRequets, garageDevice.deviceUUID)
            
    def sendProcessing(self, msgUUID, **kwargs):
        logger.info("in sendProcessing for msgUUID: %s", msgUUID)
        origMsg = self.requestsForwarded[msgUUID]
        reply = origMsg.getReply(msgStatus=kwargs.get('msgStatus',"processing"), msgStatusExtra=kwargs.get('msgStatusExtra', "Command sent to garage door controller, pending results.") )
        reply.send()

        if msgUUID in self.pendingTimers and callable(self.pendingTimers[msgUUID].cancel):
            if self.pendingTimers[msgUUID].active():
                self.pendingTimers[msgUUID].cancel()
                del self.pendingTimers[msgUUID]

        self.pendingTimers[msgUUID] = callLater(60, self.sendFailed, msgUUID)

    def sendFailed(self, msgUUID, **kwargs):
        origMsg = self.requestsForwarded[msgUUID]
        deviceUUID = origMsg['payload']['deviceUUID']

        del self.pendingRequests[deviceUUID]
        del self.requestsForwarded[msgUUID]
        del self.pendingTimers[msgUUID]

        reply = origMsg.getReply(msgStatus=kwargs.get('msgStatus',"failed"), msgStatusExtra=kwargs.get('msgStatusExtra', "Command sent to garage door controller, however, garage never reported a status change.") )
        reply.send()

    def cleanUpRequestsForwarded(self, itemID):
        if itemID in self.requestsForwarded:
          del self.requestsForwarded[itemID]

    def cleanUpPendingRequets(self, itemID):
        if itemID in self.pendingRequests:
          del self.pendingRequests[itemID]
	
