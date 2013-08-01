"""
A basic module that monitors a garage door sensor and provides
a way to pulse a relay through the control of another device.

This module monitors an input for garage door status. It can also
pulse another device to control the device.

This module assumes the garage door controller is an relay that
can be controlled with "close/open" commands.

:copyright: 2012-2013 Yombo
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

        self.ourDeviceType = "JzHX3btaHD8FBLQFlav9solU"
        self.garageDevices = {}
        self.garageInputDevices = {}

        self.requestsForwarded = {}  # track messages we have sent to control a garage
        self.pendingRequests = {} # pending requests to control a specific garage door
        self.pendingTimers = {} # various timmers....

        self.garageCommands = ('EZbM3Z01vTsV72JoGMDiBHej', 'EZbCa3HcxQwGN1wXbYXcp7pv')
                                #  open                     close 

    def load(self):
        """
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
        #setup garage door status to match input device status.

        for garageKey, garage in self.garageDevices.iteritems():
            gdevice = garage['device']
            idevice = garage['inputDevice']
            if gdevice.status[0].status != idevice.status[0].status or gdevice.status[0].statusextended != idevice.status[0].statusextended:
                gdevice.setStatus(silent=True, status=idevice.status[0].status, statusExtended=idevice.status[0].statusextended)
    
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
        Incomming Yombo Messages from the gateway or remote sources will
        be sent here.
        """

        if message.msgOrigUUID in self.requestsForwarded:
            logger.debug("in message:requestsForwarded: %s" % self.requestsForwarded[message.msgOrigUUID].dump())

#            logger.info(self.requestsForwarded[message.msgOrigUUID])
            deviceUUID = self.requestsForwarded[message.msgOrigUUID].payload['deviceobj'].deviceUUID
            garageDevice = self.garageDevices[deviceUUID]['device']


            if message.msgStatus == "processing":
                if garageDevice.deviceUUID in self.pendingTimers:
                  if callable(self.pendingTimers[garageDevice.deviceUUID].active) and self.pendingTimers[garageDevice.deviceUUID].active():
                    if self.pendingTimers[garageDevice.deviceUUID].active():
                        self.pendingTimers[garageDevice.deviceUUID].cancel()
                    del self.pendingTimers[garageDevice.deviceUUID]
                self.sendProcessing(message.msgOrigUUID, garageDevice.deviceUUID, msgStatusExtra = message.msgStatusExtra) #original being request sent to device module
            elif message.msgStatus == "failed":
                if garageDevice.deviceUUID in self.pendingTimers:
                  if callable(self.pendingTimers[garageDevice.deviceUUID].active) and self.pendingTimers[garageDevice.deviceUUID].active():
                    if self.pendingTimers[garageDevice.deviceUUID].active():
                        self.pendingTimers[garageDevice.deviceUUID].cancel()
                    del self.pendingTimers[garageDevice.deviceUUID]
                self.sendFailed(message.msgOrigUUID, garageDevice.deviceUUID, msgStatusExtra = message.msgStatusExtra) #original being request sent to device module
            return

        # if status, we only care about input devices we monitor.
        if message.msgType == 'status':
          logger.info("got message: %s", message.dump() )
          logger.info("my garage input devices: %s", self.garageInputDevices)

          if 'deviceobj' in message.payload:
            logger.info("message device devuceUUID: %s", message.payload['deviceobj'].deviceUUID)
            
            if message.payload['deviceobj'].deviceUUID in self.garageInputDevices:
              garageDevice = self.garageDevices[self.garageInputDevices[message.payload['deviceobj'].deviceUUID]]['device']
#              logger.info("668 garageDevice: %s", self.garageInputDevices[message.payload['deviceobj'].deviceUUID])
              garageDevice.setStatus(status=message.payload['status'], statusExtended=message.payload.get('statusExtended', None))
#              logger.info("garageDevice status: %s", garageDevice.status)
#              logger.info("setting garage door status for deviceuuid: %s (%s)", garageDevice.deviceUUID, message.payload['status'])
#              logger.info("checking pening requests: %s (%s)", garageDevice.deviceUUID, self.pendingRequests)
#              logger.info("checking timers: %s (%s)", garageDevice.deviceUUID, self.pendingTimers)
              if garageDevice.deviceUUID in self.pendingRequests:
#                logger.info("777 -garageDevice.deviceUUID %s", garageDevice.deviceUUID)
                origMsg = self.pendingRequests[garageDevice.deviceUUID]
                replyMsg = origMsg.getReply(msgStatus="done", msgStatusExtra=message.payload['status'])
                replyMsg.update({'msgStatusExtra' : message.msgStatusExtra})
                replyMsg.payload.update({'status' : message.payload['status'], 'statusExtended' : message.payload.get('statusExtended', None)})
                replyMsg.send()

#                logger.info("pending timmers: %s", self.pendingTimers)
                if garageDevice.deviceUUID in self.pendingTimers:
                  if callable(self.pendingTimers[garageDevice.deviceUUID].active) and self.pendingTimers[garageDevice.deviceUUID].active():
                    self.pendingTimers[garageDevice.deviceUUID].cancel()
                  del self.pendingTimers[garageDevice.deviceUUID]

                self.cleanUpRequestsForwarded(origMsg.msgUUID)
                self.cleanUpPendingRequets(garageDevice.deviceUUID)

          return # return, no more processing is required for this message type
       
        #We only care about message to us if it's not a status update.
        if message.msgDestination != self._FullName.lower():
            return  

#        device = None
        deviceUUID = ''
        deviceTypeUUID = ''
        if message.msgType == 'cmd' and message.msgStatus == 'new' and message.payload['cmdobj'].cmdUUID in self.garageCommands:
            deviceUUID = message.payload['deviceobj'].deviceUUID
            garageDevice = self.garageDevices[deviceUUID]['device']
            logger.info("qqq - garageDeviceUUID: %s", garageDevice.deviceUUID)

            if garageDevice.deviceUUID in self.pendingRequests:
                logger.info("device is already pendng.. sending failed.")
                reply = message.getReply(msgStatus="failed", msgStatusExtra="A request for this device is currently pending. Try again later." )
                reply.send()
                return


            logger.info("%%%%%%%%%%%%%%%%%%%%%%%%%%%%%")
            logger.info("garage control: %s, garage status: %s", message.payload['cmdobj'].label.lower(), garageDevice.status[0].status.lower())
            if message.payload['cmdobj'].label.lower() == garageDevice.status[0].status.lower():
                logger.info("Garage is already in request state.")
                reply = message.getReply(msgStatus="done", msgStatusExtra="No action was performed, garage is already in the requested state!" )
                reply.send()
                return

            self.pendingRequests[garageDevice.deviceUUID] = message
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
#            callLater(0.1, ctlmsg2.send)

            self.pendingTimers[garageDevice.deviceUUID] = callLater(1.05, self.sendProcessing, ctlmsg2.msgUUID, garageDevice.deviceUUID)

            # catchall to cleanup anything left over...
            callLater(1200, self.cleanUpRequestsForwarded, ctlmsg2.msgUUID)
            callLater(1200, self.cleanUpPendingRequets, garageDevice.deviceUUID)
            
    def sendProcessing(self, msgUUID, garageUUID, **kwargs):
        logger.info("in sendProcessing for msgUUID: %s", msgUUID)
        origMsg = self.requestsForwarded[msgUUID]
        reply = origMsg.getReply(msgStatus=kwargs.get('msgStatus',"processing"), msgStatusExtra=kwargs.get('msgStatusExtra', "Command sent to garage door controller, pending results.") )
        reply.send()

        if garageUUID in self.pendingTimers:
            if callable(self.pendingTimers[garageUUID].active) and self.pendingTimers[garageUUID].active():
                self.pendingTimers[garageUUID].cancel()
            del self.pendingTimers[garageUUID]

        self.pendingTimers[msgUUID] = callLater(60, self.sendFailed, msgUUID, garageUUID)

    def sendFailed(self, msgUUID, garageUUID, **kwargs):
        origMsg = self.requestsForwarded[msgUUID]
        deviceUUID = origMsg.payload['deviceobj'].deviceUUID

        reply = origMsg.getReply(msgStatus=kwargs.get('msgStatus',"failed"), msgStatusExtra=kwargs.get('msgStatusExtra', "%s Command sent to garage door controller, however, garage never reported a status change." % garageUUID ) )
        reply.send()

        self.cleanUpPendingRequets(garageUUID)
        self.cleanUpRequestsForwarded(msgUUID)
        if garageUUID in self.pendingTimers:
            del self.pendingTimers[garageUUID]

    def cleanUpRequestsForwarded(self, itemID):
        if itemID in self.requestsForwarded:
          del self.requestsForwarded[itemID]

    def cleanUpPendingRequets(self, itemID):
        if itemID in self.pendingRequests:
          del self.pendingRequests[itemID]
	
