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
        self._RegisterDistributions = ['status']
#        self._RegisterVoiceCommands = [
#          {'voiceCmd': "master garage door [close all, open all]", 'order' : 'nounverb'}
#          ]
        

        self.ourDeviceType = "JzHX3btaHD8FBLQFlav9solU"  # our virtual device type
        
        self.garageDevices = {} # mapping of VGD to real garage controllers.
        # garageDevices[VGD] = { real device and control information }
        
        self.garageInputDevices = {} # mapping of VGD to input sensors

        # Map of pending message for a given garageDeviceUUID.
        # key = garagedeviceuuid
        # Contains the orig message and msgUUID for pending command
        self.controlRequestsPending = {}

        # Map of pending message for a given garageDeviceUUID.
        # key = comand sent msg uuid
        # Contains the orig message and garagedeviceuuid for reference
        self.requestsMessagesOrig = {}
            
        # used to store timers for pending/failed messages
        self.pendingTimers = {}

        self.garageCommands = ('EZbM3Z01vTsV72JoGMDiBHej', 'EZbCa3HcxQwGN1wXbYXcp7pv')
                                #  open                     close 

    def load(self):
        """
        First, get a list of all devices we manage. validate that the commands
        we were given are valid for that device. 
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
        Sync the status of our virtual garage door (VGD) device to the input
        status device.  These should always match.
        """
        for garage in self.garageDevices:
            if self.garageDevices[garage]['device'].status[0].status != self.garageDevices[garage]['inputDevice'].status[0].status:
                status = self.garageDevices[garage]['inputDevice'].status[0].status
                statusextra = self.garageDevices[garage]['inputDevice'].status[0].statusextra
                self.garageDevices[garage]['device'].setStatus(status = status, statusExtra = statusextra, source=self._FullName)
    
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
        #logger.info("Garage got msg: %s" % message.dump())
        if message.msgType == 'status' and message['payload']['deviceobj'].deviceUUID in self.garageInputDevices:
            # Get our VGD, and then set it's status.
            garageDeviceUUID = self.garageInputDevices[message['payload']['deviceobj'].deviceUUID]
            garageDevice = self.garageDevices[garageDeviceUUID]['device']
            garageDevice.setStatus(status=message.payload['status'].status, statusExtra=message.payload['status'].statusextra, source=self._FullName)

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
                self.deleteTimer(self.controlRequestsPending[garageDevice.deviceUUID]['messageOrig'])
                if self.controlRequestsPending[garageDevice.deviceUUID]['messageOrig'] in self.requestsMessagesOrig:
                    del self.requestsMessagesOrig[self.controlRequestsPending[garageDevice.deviceUUID]['messageOrig']]
                del self.controlRequestsPending[garageDevice.deviceUUID]
            return
        

        #From here on, we only care about message to us if it's not a status update.
        if message.msgDestination != self._FullName.lower():
            logger.debug("Discarding rogue message - don't worry.")
            return  

        # toss out messages that have a bad command for us.
        if 'cmdobj' in message.payload and message.payload['cmdobj'].cmdUUID not in self.garageCommands:
            logger.warning("Discarding garage message, we don't know what that command does.")
            return  

        # toss out messages that have a bad command for us.
        if 'cmdobj' in message.payload and message.payload['deviceobj'].deviceUUID not in self.garageDevices:
            logger.warning("Unable to control a garage door not assigned to this module. Sorry.")
            return  

        # lets process cmd returns.  Treat returns as only status updates.
        # Don't send that it's really done until input senor tells us!
        if message.msgType == 'cmd' and message.msgStatus != 'new':
          if message.msgOrigUUID in self.requestsMessagesOrig:
            origMsg = self.requestsMessagesOrig[message.msgOrigUUID]['message']
            if (message.msgStatus == 'done' or message.msgStatus == 'processing'):
                theArgs = {'msgStatus' : 'processing'}
                if (origMsg.payload['cmdUUID'] == 'EZbM3Z01vTsV72JoGMDiBHej'): #opening...
                  theArgs['msgStatusExtra'] = "opening garage"
                  theArgs['textStatus'] = "Processing request to open garage."
                elif (origMsg.payload['cmdUUID'] == 'EZbCa3HcxQwGN1wXbYXcp7pv'): #closing...
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
            
            garageDeviceUUID = message['payload']['deviceobj'].deviceUUID
            garageDevice = self.garageDevices[garageDeviceUUID]['device']
            
            # If garage change is already pending, say sorry, can't now.
            if garageDevice.deviceUUID in self.controlRequestsPending:
                reply = message.getReply(msgStatus="failed", msgStatusExtra="A request for this device is currently pending. Try again later." )
                reply.send
                return

            # If garage already in requested position, let them know i'm lazy.
            if message['payload']['cmd'] == garageDevice.status[0].status:
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
            ctlmsg = controlDevice.getMessage(self, cmdUUID=self.garageDevices[garageDeviceUUID]['controlPulseStart'])

            # Undo the relay action. 
            ctlmsg2 = controlDevice.getMessage(self, cmdUUID=self.garageDevices[garageDeviceUUID]['controlPulseEnd'])

            self.controlRequestsPending[garageDevice.deviceUUID] = {
                'message' : message,
                'messageOrig' : ctlmsg2.msgUUID,
            }
            self.requestsMessagesOrig[ctlmsg2.msgUUID] = {
                'message' : message,
                'garageDeviceUUID' : garageDevice.deviceUUID,
            }

            # now we send the actual commands now that everything is setup.
            ctlmsg.send()

            #time entered is in milliseconds. Convert an int to milliseconds.
            callLater(self.garageDevices[garageDeviceUUID]['controlPulseTime']*.001, ctlmsg2.send)

            # This sends a "processing" message incase we don't get something
            # back soon. This is typical as garages take a while to close.
            self.pendingTimers[ctlmsg2.msgUUID] = callLater(0.99, self.sendProcessing, ctlmsg2.msgUUID)

            # catchall to cleanup anything left over...
            callLater(1200, self.cleanUpcontrolRequestsPending, garageDevice.deviceUUID)
            callLater(1200, self.cleanUprequestsMessagesOrig, ctlmsg2.msgUUID)
            
    def sendProcessing(self, msgUUID, **kwargs):
        origMsg = self.requestsMessagesOrig[msgUUID]['message']
        reply = origMsg.getReply(msgStatus=kwargs.get('msgStatus',"processing"), msgStatusExtra=kwargs.get('msgStatusExtra', "Command sent to garage door controller, pending results.") )
        reply.send()

        self.deleteTimer(msgUUID)
        
        self.pendingTimers[msgUUID] = callLater(60, self.sendFailed, msgUUID)

    def sendFailed(self, msgUUID, **kwargs):
        origMsg = self.requestsMessagesOrig[msgUUID]['message']
        logger.info("send failed?? %s" % origMsg.dump())
        deviceUUID = origMsg['payload']['deviceobj'].deviceUUID

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
	
