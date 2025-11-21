import select
import socket
import queue
import textwrap
#import libscrc
import threading
import time
import http.server
import json, codecs
from itertools import cycle
#from io import BytesIO
from datetime import datetime
from urllib.parse import urlparse, parse_qs, parse_qsl
from collections import defaultdict
import logging
import os, psutil
from grottconf import Conf
from grottdata import procdata

#set logging definities
#logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# grottserver.py emulates the server.growatt.com website and is initial developed for debugging and testing grott.
# Updated: 2025-04-11
# Version:
vrmserver = "3.2.1_20250608"

# Declare Variables (to be moved to config file later)
#serverhost = "0.0.0.0"
#serverport = 5781
httphost = "0.0.0.0"
#httpport = 5782
verbose = True
#firstping = False
sendseq = 1
#Time to sleep waiting on API response
#apirespwait = 0.1
#Totaal time in seconds to wait on Iverter Response
#inverterrespwait = 10

#Totaal time in seconds to wait on Datalogger Response
#dataloggerrespwait = 5
#ConnectionTimeout = 300 is now configurable in grott.ini

from enum import Enum
class CommandType(Enum):
    InverterReadRegisters = "05"
    InverterWriteSingleRegister = "06"
    InverterWriteMultiRegisters = "10"
    DataLoggerWriteRegisters = "18"
    DataLoggerReadRegisters = "19"

def addLoggingLevel(levelName, levelNum, methodName=None):
    if not methodName:
        methodName = levelName.lower()

    if hasattr(logging, levelName):
        raise AttributeError("{} already defined in logging module".format(levelName))
    if hasattr(logging, methodName):
        raise AttributeError("{} already defined in logging module".format(methodName))
    if hasattr(logging.getLoggerClass(), methodName):
        raise AttributeError("{} already defined in logger class".format(methodName))

    def logForLevel(self, message, *args, **kwargs):
        if self.isEnabledFor(levelNum):
            self._log(levelNum, message, args, **kwargs)

    def logToRoot(message, *args, **kwargs):
        logging.log(levelNum, message, *args, **kwargs)

    logging.addLevelName(levelNum, levelName)
    setattr(logging, levelName, levelNum)
    setattr(logging.getLoggerClass(), methodName, logForLevel)
    setattr(logging, methodName, logToRoot)


# Formats multi-line data
def format_multi_line(prefix, string, size=80):
    size -= len(prefix)
    if isinstance(string, bytes):
        string = ''.join(r'\x{:02x}'.format(byte) for byte in string)
        if size % 2:
            size -= 1
    return '\n'.join([prefix + line for line in textwrap.wrap(string, size)])


# encrypt / decrypt data.
def decrypt(decdata):

    ndecdata = len(decdata)

    # Create mask and convert to hexadecimal
    mask = "Growatt"
    hex_mask = ['{:02x}'.format(ord(x)) for x in mask]
    nmask = len(hex_mask)

    # start decrypt routine
    unscrambled = list(decdata[0:8])  # take unscramble header

    for i, j in zip(range(0, ndecdata-8), cycle(range(0, nmask))):
        unscrambled = unscrambled + [decdata[i+8] ^ int(hex_mask[j], 16)]

    result_string = "".join("{:02x}".format(n) for n in unscrambled)

    logger.debugv("\t - " + "Grott - data decrypted V2")
    return result_string

def calc_crc(data):
    #calculate CR16, Modbus.
    crc = 0xFFFF
    for pos in data:
        crc ^= pos
        for i in range(8):
            if ((crc & 1) != 0):
                crc >>= 1
                crc ^= 0xA001
            else:
                crc >>= 1
    return crc

def validate_record(xdata):
    # validata data record on length and CRC (for "05" and "06" records)
    logger.debug("validate data record")
    data = bytes.fromhex(xdata)
    ldata = len(data)
    len_orgpayload = int.from_bytes(data[4:6],"big")
    header = "".join("{:02x}".format(n) for n in data[0:8])
    protocol = header[6:8]

    if protocol in ("05","06"):
        lcrc = 4
        crc = int.from_bytes(data[ldata-2:ldata],"big")
    else:
        lcrc = 0

    len_realpayload = (ldata*2 - 12 -lcrc) / 2

    if protocol != "02" :
                crc_calc = calc_crc(data[0:ldata-2])

    if len_realpayload == len_orgpayload :
        returncc = 0
        if protocol != "02" and crc != crc_calc:
            returncc = 8
    else :
        returncc = 8

    return(returncc)

def htmlsendresp(self, responserc, responseheader,  responsetxt) :
        #send response
        self.send_response(responserc)
        self.send_header('Content-type', responseheader)
        self.end_headers()
        self.wfile.write(responsetxt)
        logger.debug(f"Grotthttpserver - http response send:  {responserc}, {responseheader}, {responsetxt}")

def createtimecommand(self, protocol,deviceid,loggerid,sequenceno) :
        protocol = protocol
        loggerid = loggerid
        #override diviceid (always send time to logger)
        deviceid = "01"
        sequenceno = sequenceno
        bodybytes = loggerid.encode('ISO-8859-1')
        body = bodybytes.hex()
        if protocol == "06" :
            body = body + "0000000000000000000000000000000000000000"
        register = 31
        body = body + "{:04x}".format(int(register))
        currenttime = str(datetime.now().replace(microsecond=0))
        timex = currenttime.encode('ISO-8859-1').hex()
        timel = "{:04x}".format(int(len(timex)/2))
        body = body + timel + timex
        #calculate length of payload = body/2 (str => bytes) + 2 bytes invertid + command.
        bodylen = int(len(body)/2+2)

        #create header
        header = "0001" + "00" + protocol + "{:04x}".format(bodylen) + deviceid + "18"
        #print(header)
        body = header + body
        body = bytes.fromhex(body)
        logger.debug(f'Time plain body : {format_multi_line("  ",body)}')

        if protocol != "02" :
            #encrypt message
            body = decrypt(body)
            crc16 = calc_crc(bytes.fromhex(body))
            body = bytes.fromhex(body) + crc16.to_bytes(2, "big")

        logger.debug(f'Time command created  : {format_multi_line("  ",body)}')

        return(body)

class registerInfo:
    def __init__(self, regno, value, retrievalDate=None):
        self.regno = regno
        self.retrievalDate = retrievalDate if retrievalDate else datetime.now()
        self.value = value
        # Metadata fields populated from register JSON (name, description, unit, readOnStart, etc.)
        self.name = None
        self.description = None
        self.unit = None
        self.readOnStart = None

    def __str__(self):
        return f"Register {self.regno}: Value={self.value}, RetrievedAt={self.retrievalDate}, Name={self.name}, Description={self.description}, Unit={self.unit}, ReadOnStart={self.readOnStart}"

from registerTool import RegisterImporter
class allRegistersInfo:
    def __init__(self,name):
        self.registerInfos = {}
        filename = name + ".json"
        importer = RegisterImporter()
        file_path = os.path.join('registers', filename)
        try:
            with open(file_path, 'r') as f:
                pass
        except FileNotFoundError:
            logger.warning(f"Register file not found for {name} at {file_path}")
            return  
        imported_registers = importer.import_from_json(file_path)
        if not imported_registers:
            logger.warning(f"No registers imported for {name} from {file_path}")
            return
        
        for reg in imported_registers:
            regno = reg.get('register')
            value = reg.get('value', '')
            regInfo = registerInfo(regno, value) 
            self.registerInfos[regno] = regInfo
            for regField in reg:
                if regField not in ('register', 'value'):
                    setattr(self.registerInfos[regno], regField, reg[regField])

    
    def add_register(self, register_info):
        self.registerInfos[register_info.regno] = register_info
    
    def update_register(self, regno, value):
        if regno in self.registerInfos:
            self.registerInfos[regno].value = value
            self.registerInfos[regno].retrievalDate = datetime.now()
        else:
            self.registerInfos[regno] = registerInfo(regno, value)
    
    def get_register_response(self, register):
        if register in self.registerInfos:
            return self.registerInfos[register]
        else:
            return None

    def check_register_response(self, regno, startTimeStamp):
        if regno in self.registerInfos:
            reg_info = self.registerInfos[regno]
            if reg_info.retrievalDate >= startTimeStamp:
                return True
        return False
    
    def get_register_response_if_valid(self, register, startTimeStamp):
        """
        Optimized method: check and retrieve register in one call.
        Returns the registerInfo if valid (exists and updated after startTimeStamp), else None.
        Eliminates redundant double-lookup.
        """
        if register in self.registerInfos:
            reg_info = self.registerInfos[register]
            if reg_info.retrievalDate >= startTimeStamp:
                return reg_info
        return None
    
    def idType(self):
        return 'Undefined'
    
    def id(self):
        return 'Undefined'
    
    def createRegOverview(self, items):
        for regInfo in self.registerInfos.values():
            items.append({
                        'idType': self.idType(),
                        'id': self.id(),
                        'regno': regInfo.regno,
                        'name': getattr(regInfo, 'name', None),
                        'description': getattr(regInfo, 'description', None),
                        'unit': getattr(regInfo, 'unit', None),
                        'readOnStart': getattr(regInfo, 'readOnStart', None),
                        'retrievalDate': regInfo.retrievalDate,
                        'value': regInfo.value
                })
    

class inverterInfo(allRegistersInfo):
    def __init__(self, inverterid, dataloggerid, inverterno):
        self.inverterid = inverterid
        self.dataloggerid = dataloggerid
        self.inverterno = inverterno
        super().__init__(inverterid)
    
    def idType(self):
        return 'Inverter'
    
    def id(self):
        return self.inverterid
    
class loggerInfo(allRegistersInfo):
    def __init__(self, dataloggerid, protocol, ip, port):
        self.dataloggerid = dataloggerid
        self.protocol = protocol
        self.ip = ip
        self.port = port
        self.inverters = {}
        self._inverterno_cache = {}  # Cache to speed up get_inverter_byinverterno lookups
        super().__init__(dataloggerid)
    
    def idType(self):
        return 'Datalogger' 
    
    def id(self):
        return self.dataloggerid
        
    def add_inverter(self, inverter):
        self.inverters[inverter.inverterid] = inverter
        # Update cache for O(1) lookup by inverterno
        self._inverterno_cache[inverter.inverterno] = inverter
    
    def inverter_exists(self, inverterid):
        return inverterid in self.inverters
    
    def get_inverter(self, inverterid):
        return self.inverters.get(inverterid, None) 
    
    def get_inverter_byinverterno(self, inverterno):
        """
        Optimized: Use cached lookup instead of O(n) scan.
        Cache is populated when inverters are added.
        Falls back to scan if cache lookup fails (for robustness).
        """
        # Try cache first
        if inverterno in self._inverterno_cache:
            return self._inverterno_cache[inverterno]
        
        # Fallback: scan and rebuild cache (shouldn't normally happen, but handles edge cases)
        logger.debug(f"Cache miss for inverterno {inverterno}, rebuilding from inverters dict")
        for inverterid, inverter in self.inverters.items():
            if inverter.inverterno == inverterno:
                # Add to cache for next time
                self._inverterno_cache[inverterno] = inverter
                return inverter
        return None
    
    def getinverterno(self, name=None):
        if name and name in self.inverters:
            inverter = self.get_inverter(name)
            return inverter.inverterno
        else:
            return '01' # Default deviceid for datalogger

    def update_inverter_register(self, inverterno, regno, value):
        inverter = self.get_inverter_byinverterno(inverterno)
        if inverter:
            inverter.update_register(regno, value)
        else:
            logger.warning(f"Datalogger {self.dataloggerid}: did not find inverterno {inverterno} Updating inverter register {regno} with value {value} unsuccessful")

class loggerRegistry: 
    def __init__(self):
        self.loggers = {}
        self.inverters = {}

    def add_logger(self, dataloggerid, protocol, ip, port):
        if dataloggerid not in self.loggers:
            logger.info(f"Adding logger: {dataloggerid}, Protocol: {protocol}, IP: {ip}, Port: {port}")
            self.loggers[dataloggerid] = loggerInfo(dataloggerid, protocol, ip, port)
        else:
            logger.warning(f"Logger with ID {dataloggerid} already exists. Skipping addition.")

    def update_logger(self, dataloggerid, protocol, ip, port):
        if dataloggerid not in self.loggers:
            logger.info(f"Adding logger: {dataloggerid}, Protocol: {protocol}, IP: {ip}, Port: {port}")
            self.loggers[dataloggerid] = loggerInfo(dataloggerid, protocol, ip, port)
        elif (self.loggers[dataloggerid].protocol != protocol or self.loggers[dataloggerid].ip != ip or self.loggers[dataloggerid].port != port):
            logger.info(f"Updating logger: {dataloggerid}, Protocol: {protocol}, IP: {ip}, Port: {port}")
            self.loggers[dataloggerid].protocol = protocol
            self.loggers[dataloggerid].ip = ip
            self.loggers[dataloggerid].port = port  


    def add_inverter(self, dataloggerid, inverterid, inverterno):
        if inverterid not in self.inverters:
            logger.info(f"Adding inverter: {inverterid} with number {inverterno} to registry")
            inverter = inverterInfo(inverterid, dataloggerid, inverterno)
            self.inverters[inverterid] = inverter
        else:
            inverter = self.inverters[inverterid]
        if dataloggerid in self.loggers and not self.loggers[dataloggerid].inverter_exists(inverterid):
            logger.info(f"Adding inverter: {inverterid} with number {inverterno} to logger {dataloggerid}")
            self.loggers[dataloggerid].add_inverter(inverter)
    
    def getQueueName(self, dataloggerid):
        if dataloggerid in self.loggers:
            logger = self.loggers[dataloggerid]
            return logger.ip + "_" + str(logger.port)
        else:
            raise KeyError(f"Datalogger ID {dataloggerid} not found")

    def __getitem__(self, dataloggerid):
        if dataloggerid in self.loggers:
            return self.loggers[dataloggerid]
        else:
            raise KeyError(f"Datalogger ID {dataloggerid} not found")

    def find_datalogger_by_inverter(self, inverterid):
        for dataloggerid, logger in self.loggers.items():
            if inverterid in logger.inverters:
                return logger
        return None

    def get_datalogger(self,name):
        if name in self.loggers:
            return self.loggers[name]
        else:
            return self.find_datalogger_by_inverter(name)
        return None

    def get_inverter(self, name):
        """Get an inverter by inverter ID from the registry"""
        if name in self.inverters:
            return self.inverters[name]
        return None

    def update_datalogger_register_response(self, dataloggerid, regno, value):
        datalogger = self.get_datalogger(dataloggerid)
        if datalogger:
            datalogger.update_register(regno, value)
        else:
            logger.warning(f"Datalogger ID {dataloggerid} not found. Cannot update register {regno}.")
    
    def update_inverter_register_response(self, dataloggerid, inverterno, regno, value):
        datalogger = self.get_datalogger(dataloggerid)
        if datalogger:
            datalogger.update_inverter_register(inverterno, regno, value)
        else:
            logger.warning(f"Datalogger ID {dataloggerid} not found. Cannot update inverter {inverterno} register {regno}.")

def getQueueName(datalogger):
    return datalogger.ip + "_" + str(datalogger.port)

loggerreg = loggerRegistry()

class commandInfo:
    def __init__(self, name, readCommand):
        self.datalogger = loggerreg.get_datalogger(name)
        self.inverter = loggerreg.get_inverter(name)
        self.error = None
        if name in loggerreg.loggers:
            if readCommand:
                self.sendcommand = CommandType.DataLoggerReadRegisters
            else:
                self.sendcommand = CommandType.DataLoggerWriteRegisters
        elif name in loggerreg.inverters:
            if readCommand:
                self.sendcommand = CommandType.InverterReadRegisters
            else:
                self.sendcommand = CommandType.InverterWriteSingleRegister
        else:
            self.error = (f"Name {name} not found in logger or inverter registry")

def queueRegisterCommand(send_queuereg, cmdInfo, startregister=None, endregister=None, value=None):
    """
    Queue a register command (GET or PUT).
    
    For GET commands (InverterReadRegisters, DataLoggerReadRegisters): reads register value
    For PUT commands (InverterWriteSingleRegister, InverterWriteMultiRegisters, DataLoggerWriteRegisters): writes value to register
    
    Args:
        send_queuereg: queue registry
        datalogger: datalogger info object
        sendcommand: command type (CommandType enum or string '05', '06', '10', '18', '19')
        register: register number (for single register commands)
        value: value to write (for write commands; None for read commands)
        startregister: start register (for multiregister command)
        endregister: end register (for multiregister command)
    """
    # cmdInfo is expected to be an instance of commandInfo
    if not cmdInfo or getattr(cmdInfo, 'error', None):
        logger.warning("Invalid cmdInfo provided to queueRegisterCommand: %s", getattr(cmdInfo, 'error', None))
        return None

    sendcommand = cmdInfo.sendcommand
    # Support both enum and string for backward compatibility
    if isinstance(sendcommand, CommandType):
        cmd_str = sendcommand.value
    else:
        cmd_str = sendcommand

    datalogger = cmdInfo.datalogger
    protocol = datalogger.protocol
    loggerid = datalogger.dataloggerid
    sendseq = 1
    
    # Build body starting with logger ID
    bodybytes = loggerid.encode('ISO-8859-1')
    body = bodybytes.hex()
    
    if protocol == "06":
        body = body + "0000000000000000000000000000000000000000"
    
    # Handle different command types
    if cmd_str == CommandType.InverterWriteMultiRegisters.value:
        # Multiregister write (startregister, endregister, value)
        body = body + "{:04x}".format(int(startregister)) + "{:04x}".format(int(endregister)) + value
    elif cmd_str == CommandType.InverterWriteSingleRegister.value:
        # Inverter register write (register, value in hex format)
        value_hex = "{:04x}".format(int(value))
        body = body + "{:04x}".format(int(startregister)) + value_hex
    elif cmd_str == CommandType.DataLoggerWriteRegisters.value:
        # Datalogger register write (register, value_length, value)
        value_hex = value.encode('ISO-8859-1').hex()
        valuelen = int(len(value_hex) / 2)
        body = body + "{:04x}".format(int(startregister)) + "{:04x}".format(valuelen) + value_hex
    else:
        # Read commands (InverterReadRegisters, DataLoggerReadRegisters): register start and end are same
        body = body + "{:04x}".format(int(startregister)) + "{:04x}".format(int(endregister if endregister is not None else startregister))
    
    # Calculate body length
    bodylen = int(len(body) / 2 + 2)
    
    # Determine device ID
    deviceid = "01"  # Default for datalogger
    if cmd_str in (CommandType.InverterReadRegisters.value, CommandType.InverterWriteSingleRegister.value, CommandType.InverterWriteMultiRegisters.value):  # Inverter commands
        # For inverter commands, prefer to use the inverter-specific number if available
        try:
            # cmdInfo.inverter may be an inverterInfo or None; loggerreg.get_datalogger returns loggerInfo
            deviceid = datalogger.getinverterno()
        except Exception:
            deviceid = "01"
    
    logger.info(f"Selected deviceid: {deviceid} protocol: {protocol}")
    messageSeqNo = "{:04x}".format(sendseq)
    header = messageSeqNo + "00" + protocol + "{:04x}".format(bodylen) + deviceid + cmd_str
    body = header + body
    body = bytes.fromhex(body)
    
    logger.info(f'Unencrypted Plain body : {format_multi_line("  ",body)}')
    
    # Encrypt if needed
    if protocol != "02":
        body = decrypt(body)
        crc16 = calc_crc(bytes.fromhex(body))
        body = bytes.fromhex(body) + crc16.to_bytes(2, "big")
        logger.info(f'Encrypted Plain body : {format_multi_line("  ",body)}')
    
    # Queue the command
    qname = getQueueName(datalogger)
    send_queuereg[qname].put(body)
    logger.info(f"{qname} - command queued, body {body} datalogger {loggerid} startregister {startregister} endregister {endregister} command {cmd_str}")
    return datetime.now()

   

def getRegisterValue(startTimeStamp, cmdInfo, register):
    # cmdInfo should contain datalogger and sendcommand
    if not cmdInfo or getattr(cmdInfo, 'error', None):
        logger.warning("Invalid cmdInfo provided to getRegisterValue: %s", getattr(cmdInfo, 'error', None))
        return registerInfo(register, getattr(cmdInfo, 'error', 'Invalid cmdInfo'))

    sendcommand = cmdInfo.sendcommand
    if isinstance(sendcommand, CommandType):
        cmd_str = sendcommand
    else:
        # allow raw string as fallback
        cmd_str = sendcommand

    datalogger = cmdInfo.datalogger

    if cmd_str == CommandType.InverterReadRegisters:
        wait = round(conf.inverterrespwait/conf.apirespwait)
    else:
        wait = round(conf.dataloggerrespwait/conf.apirespwait)

    logging.info(f"Waiting for command response: {wait} cycles of {conf.apirespwait} seconds each")
    register = int(register)
    for x in range(wait):
        logging.info(f"Waiting for command response, cycle {x+1} of {wait}")
        inverter = cmdInfo.inverter
        if inverter:
            # Use optimized get_register_response_if_valid to avoid double lookup
            reg_info = inverter.get_register_response_if_valid(register, startTimeStamp)
            if reg_info:
                return reg_info

        # Check datalogger (also optimized)
        reg_info = datalogger.get_register_response_if_valid(register, startTimeStamp)
        if reg_info:
            return reg_info
        
        # Set retry waiting cycle time loop for datalogger or inverter
        time.sleep(conf.apirespwait)
    
    logging.warning("No valid response received within the wait time")
    return registerInfo(register, "No valid response received")

# Unified function for queueing a register command and getting the response
def queueAndGetRegisterValue(send_queuereg, name, readCommand, startregister, endregister=None, value=None):
    """
    Queue a register command (read or write) and wait for the response.
    Args:       send_queuereg: queue registry  
        name: datalogger name or inverter id
        readCommand: True for read, False for write
        startregister: register number (for single register commands) or start register (for multiregister command)
        value: value to write (for write commands; None for read commands)
        endregister: end register (for multiregister command; None for single register commands)
    """
      
    cmdInfo = commandInfo(name, readCommand)
    if cmdInfo.error:
        return registerInfo(startregister, cmdInfo.error)
    startTimeStamp = queueRegisterCommand(send_queuereg, cmdInfo, startregister=startregister, endregister=endregister, value=value)
    returnVal = getRegisterValue(startTimeStamp, cmdInfo, startregister)
    return returnVal


# GrottHttpRequestHandler and GrottHttpServer replaced by FlaskServer - classes deleted

class recordInfo: 
    def __init__(self, origData):
        self.origData = origData
        self.header = origData[0:8].hex() # use .hex() for bytes-like objects (faster and clearer) than "".join("{:02x}".format(n) for n in origData[0:8])
        self.sequencenumber = self.header[0:4]
        self.protocol = self.header[6:8]
        self.datalength = int(self.header[8:12],16)
        self.deviceid = self.header[12:14]
        self.rectype = self.header[14:16] 
        if self.protocol in ("05","06") :
            self.decryptedData = decrypt(self.origData)
        else:
            self.decryptedData =  self.origData.hex()
        self.loggerid = codecs.decode(self.decryptedData[16:36], "hex").decode('ISO-8859-1')
        self.inverterid = ""
        logger.debug(f"Header: {self.header}")
        logger.info(f"Record type: {self.rectype} loggerid: {self.loggerid} deviceid: {self.deviceid} protocol: {self.protocol} seqno: {self.sequencenumber}")
    
    def validRecord(self):
        # validate record length
        if self.protocol in ("05","06") :
            lcrc = 8
        else :
            lcrc = 6
        if self.datalength + lcrc != len(self.origData):
            logger.warning(f"Invalid Record (length) detected: Expected: {self.datalength + lcrc} Received: {len(self.origData)}")
            return False
        # validate crc for protocol 05 and 06
        if self.protocol in ("05","06") :
            crccalc = calc_crc(self.origData[0: self.datalength + 6])
            crcrec = int.from_bytes(self.origData[self.datalength + 6:self.datalength + 8],"big")
            if crccalc != crcrec :
                logger.warning(f"Invalid Record (CRC) detected: Expected: {crcrec:04x} Calculated: {crccalc:04x}")
                return False
        return True
    
    def infoStr(self): 
        return f"recordInfo - Header: {self.header} - Protocol: {self.protocol} - DeviceID: {self.deviceid} - RecordType: {self.rectype} LoggerID: {self.loggerid} - InverterID: {self.inverterid} - DataLength: {self.datalength}"
    
    def debugOrigData(self):
        return f"recordInfo - OrigData: \n{format_multi_line('\t',self.origData)}"
    
    def debugDecryptedData(self):
        return f"recordInfo - DecryptedData: \n{format_multi_line('\t',bytes.fromhex(self.decryptedData))}"
    
    def setInverterID(self):
        if self.protocol in ("02","05") :
            inverterEncoded = self.decryptedData[36:56]
        else :
            inverterEncoded =  self.decryptedData[76:96]
        self.inverterid = codecs.decode(inverterEncoded, "hex").decode('ISO-8859-1')
        
    
class sendrecvserver:
    def __init__(self, conf, host, port, send_queuereg):
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.setblocking(0)
        self.server.bind((host, port))
        self.server.listen(5)

        self.inputs = {}
        self.outputs = {}
        self.exceptional ={}
        self.send_queuereg = send_queuereg
        self.channel = {}
        self.lastmessage = {}                                                   #to detect inactive connections

        logger.info(f"Grottserver - Ready to listen at: {host}:{port}")

    def run(self,conf):
        logger.info("Grottserver - server listening")
        trname = conf.serverip+"_"+str(conf.serverport)
        self.inputs[trname] = [self.server]
        self.outputs[trname] = [self.server]
        self.exceptional[trname] = [self.server]
        while self.inputs:
            readable, writable, exceptional = select.select(
                self.inputs[trname], self.outputs[trname], self.exceptional[trname])

            for s in readable:
                self.handle_readable_socket(conf,s,trname)

            for s in writable:
                self.handle_writable_socket(conf,s,trname)

            for s in exceptional:
                self.handle_exceptional_socket(conf,s,trname)

    def handle_client(self,conf,conn,qname,trname):
        # Start seperate thread per connectiob

        s = conn
        self.inputs[qname] = [s]
        self.outputs[qname] = [s]
        self.exceptional[qname] = [s]

        try:
            logger.debug("[thread: {0}] starting".format(trname))

            while self.inputs[qname]:

                if s.fileno() == -1 :
                    logger.debug("handle_client({0}), socket closed".format(trname))
                    break
                readable, writable, exceptional = select.select(
                self.inputs[qname], self.outputs[qname], self.exceptional[qname])

                for s in readable:
                    self.handle_readable_socket(conf,s,trname)

                for s in writable:
                    self.handle_writable_socket(conf,s,trname)

                for s in exceptional:
                    self.handle_exceptional_socket(conf,s)
        except Exception as e:
            logger.info("Socket closed: %s", e)

        logger.info("thread for: {0} ending".format(trname))

    def handle_readable_socket(self, conf, s, trname):
        logger.info("handle_readble_socket, input received on socket : %s",s)
        # test if comming from growatt server and set flag

        try:
            if s is self.server:
                logger.info("handle_readable_socket, no connection with peer yet, will be established")
                self.handle_new_connection(conf,s)

            else:
                # Existing connection
                try:
                    #read buffer until empty!!!!
                    msgbuffer = b''
                    buffsize = 1024
                    while True:
                        part = s.recv(buffsize)
                        msgbuffer += part
                        if len(part) < buffsize:
                        # either 0 or end of data
                            break
                    logger.debug(f"received data before buffersplit processing:")
                    logger.debug("\n{0}".format(format_multi_line("\t", msgbuffer)))
                    #process the data
                    if msgbuffer:
                        #split buffer if contain multiple records
                        logger.debug("start message buffer processing")
                        reclength = int.from_bytes(msgbuffer[4:6],"big")
                        buflength = len(msgbuffer)
                        header = "".join("{:02x}".format(n) for n in msgbuffer[0:8])
                        protocol = header[6:8]
                        #set recordlength correction header + crc (if included).
                        if protocol in ("05","06"):
                           lcrc = 8
                        else:
                            lcrc = 6
                        buflength = len(msgbuffer)
                        if reclength + lcrc > buflength:
                            logger.debug("Invalid Record (length) detected")
                            return
                        while reclength + lcrc <= buflength:
                            logger.debug("Process message buffer split processing")
                            #get first message
                            data = msgbuffer[0:reclength+lcrc]
                            #set last reference time (for removing inactive connections)
                            self.lastmessage[s] = time.time()
                            recInfo = recordInfo(data)

                            # pass data to growatt
                            try:
                                if conf.serverpassthrough:
                                    sRaddr = s.getpeername()
                                    if sRaddr[0] == self.forwardip and sRaddr[1] == int(self.forwardport):
                                        if conf.fullproxy:
                                            logger.info("handle_readble_socket, fullproxy enabled, sent data to client")
                                            #forward all data to client
                                            gLaddr = self.channel[s].getpeername()
                                            qname = gLaddr[0]+"_"+str(gLaddr[1])
                                            try:
                                                logger.info(f"fullproxy, put data {data} on client queue: {qname}")
                                                self.send_queuereg[qname].put(data)
                                                logger.debug("fullproxy, data forwarded to client")
                                            except Exception as e:
                                                logger.warning("fullproxy, exception in data forwarding %s", e)
                                        else:
                                            logger.info("handle_readble_socket, data from growatt server will be ignored")
                                            #no further processing needed
                                        return()
                                    else:
                                        logger.debug("handle_readble_socket, process data to sent to growatt server")

                                        if conf.fullproxy or recInfo.rectype in ("03", "04", "16","50", "1b", "19","20","29"):
                                            #forward only specific recordtypes, in full proxy all records are forwarded
                                            #get qname for growatt server based on growatt address and client addres
                                            gLaddr = self.channel[s].getsockname()
                                            qname = gLaddr[0]+"_"+str(gLaddr[1])

                                            try:
                                                logger.info("handle_readble_socket, put data on growatt queue: %s",qname)
                                                self.send_queuereg[qname].put(data)
                                            except Exception as e:
                                                logger.warning("handle_readble_socket, exception in data forwarding %s", e)
                                                return()

                                            logger.debug("handle_readble_socket, data forwarded to growatt server")
                                            #wait for ack from growatt server
                                            if recInfo.rectype == "03":
                                                self.waitsync(recInfo.seqno,s,2)
                                            #    time.sleep(0.1)
                                        else:
                                            logger.debug("handle_readble_socket, data filtered and not forwarded to growatt server:")

                            except Exception as e:
                                logger.warning("handle_readble_socket, Growatt passthrough error: %s",e)
                                logger.warning("handle_readble_socket, continue without forwarding")

                            #Process the data
                            self.process_data(conf,s, recInfo)
                            #create buffer with remaining messages
                            if buflength > reclength+lcrc:
                                logger.debug("handle_readble_socket, process additional messages in buffer")
                                msgbuffer = msgbuffer[reclength+lcrc:buflength]
                                reclength = int.from_bytes(msgbuffer[4:6],"big")
                                buflength = len(msgbuffer)
                            else: break
                    else:
                        # Empty read means connection is closed, perform cleanup
                        logger.warning("handle_readble_socket, empty read, close connection")
                        self.close_connection(conf,s)

                #except ConnectionResetError:
                except Exception as e:
                    logger.warning("handle_readble_socket, ConnectionResetError exception: %s",e)
                    #close client and passthrough connection
                    self.close_connection(conf,s)

        except Exception as e:
            logger.warning("handle_readble_socket, generic exception, closing connection: %s",e)
            self.close_connection(conf,s)


    def handle_writable_socket(self, conf, s, trname):
        #logger.debug("handle_writable_socket for: %s",s)
        try:
            #with print statement no crash, without crash, does sleep solve this problem ?
            time.sleep(0.1)

            if s.fileno() == -1 :
                logger.info("handle_writable_socket ({0}), socket already closed".format(trname))
                return
            try:
                #try for debug 007
                client_address, client_port = s.getpeername()
                if conf.serverpassthrough:
                    if client_address == self.forwardip and client_port == int(self.forwardport):
                        client_address, client_port = s.getsockname()
            except Exception as e:
                logger.warning("handle_writable_socket, socket error: %s",e)
                self.close_connection(conf,s)
                return

            try:
                qname = client_address + "_" + str(client_port)
                next_msg = self.send_queuereg[qname].get_nowait()
                logger.debug("handle_writable_socket, get response from queue: %s \n", qname)
                logger.debug("\n{0}".format(format_multi_line("\t",next_msg,80)))
                s.send(next_msg)

            except queue.Empty:
                #Do not activate logger entry here will full-up the log!!!!!
                #wait before handling next message
                #time.sleep(0.1)
                pass

            # calculate last message send and close connection if iddle for more then 90s (ping frequency)
            try:
                logger.debugv("handle_writable_connection, Start calculate timeout routine %s", s)

                try:
                    timeout =  time.time()-self.lastmessage[s]
                    logger.debugv("handle_writable_connection: {0}, timer: {1}, connectiontimeout: {2}".format(trname,timeout,conf.ConnectionTimeout))
                except Exception as e:
                    #no lastmessage for this connection yet, set lastmessage skip processing
                    self.lastmessage[s] = time.time()
                    timeout =  0

                if timeout > conf.ConnectionTimeout:

                    logger.debug("time out > {0} for {1}".format(conf.ConnectionTimeout,s))
                    if s.fileno() != -1 :
                        logger.info("handle_writable_connection, inactive socket will be closed: {0}, {1}".format({s}, {timeout}))
                        self.close_connection(conf,s)
                    else :
                        logger.info(f"handle_writable_connection, inactive socket already closed: {s}")

            except Exception as e:
                logger.debug("handle_writable_connection, error in calculate timeout routine %s",e)

        except Exception as e:
            logger.warning("handle_writable_socket, exception: %s", e)
            self.close_connection(conf,s)


    def handle_exceptional_socket(self, conf, s):
        logger.warning("handle_exceptional_socket, exception or socket: %s", s)
        self.close_connection(conf,s)

    def handle_new_connection(self, conf, s):
        try:
            logger.debug("handle_new_connection, new connection request received: \n\t %s",s)
            connection, client_address = s.accept()
            #connection.setblocking(0)

            client_address, client_port = connection.getpeername()
            qname = client_address + "_" + str(client_port)

            #create queue
            self.send_queuereg[qname] = queue.Queue()
            logger.info("handle_new_connection, send queue created for: %s", qname)

            if conf.serverpassthrough:
                #start thread for handling forward connection before client thread!!
                forward = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                forward.connect((conf.growattip, conf.growattport))
                #get actual growatt server address if DNS name is used in .ini settings
                self.forwardip, self.forwardport = forward.getpeername()
                logger.info(f"forward, connection with growatt server established: {conf.growattip}:{conf.growattport} ip is  {self.forwardip}:{self.forwardport}")
                logger.info(f"forward, socket: {forward}")
                gLaddr = forward.getsockname()
                gqname =  gLaddr[0] + "_" + str(gLaddr[1])
                self.send_queuereg[gqname] = queue.Queue()
                logger.info("handle_new_connection, forward queue created for : %s", gqname)

                #create forward channel pair
                self.channel[connection] = forward
                self.channel[forward] = connection
                #self.gwserver =  Forward.start(self,conf.growattip, conf.growattport)
                trfname = "forward_"+ gLaddr[0] + ":" + str(gLaddr[1])
                tf = threading.Thread(target=self.handle_client, args=[conf,forward,gqname,trfname],name=trfname)
                tf.start()


            # create processing thread
            trname = "client_"+ client_address + ":" + str(client_port)
            t = threading.Thread(target=self.handle_client, args=[conf,connection,qname,trname],name=trname)
            t.start()
            #


        except Exception as e:
            logger.warning("handle_new_connection exception:  %s \n\t", e)

    def close_connection(self, conf, s):
        logger.debug("Close Connection for socket: {0}".format(s))

        #retrieve information about connection
        logger.debugv("retrieve connection information")

        close_fd = s.fileno()
        for x in self.inputs :

                if  close_fd == self.inputs[x][0].fileno() :
                    qname = x
                    break

        try:
            logger.debug("Close Connection for socket: {0}".format(s))
            #Test if endpoint still exist?
            s.close()
        except Exception as e:
            logger.debug("close connection error:  %s", e)

        try:
            logger.debugv("clean connection queues")
            if qname in self.outputs:
                del self.outputs[qname]
            if qname in self.inputs:
                del self.inputs[qname]
            if qname in self.exceptional:
                del self.exceptional[qname]
            if s in self.lastmessage :
                del self.lastmessage[s]
            del self.send_queuereg[qname]
        except Exception as e:
            logger.debugv("clean connection queues error:  %s", e)

        if conf.serverpassthrough:
            logger.debug("Close  Passthough Connection")
            pt_socket = self.channel[s]
            pt_address, pt_port = pt_socket.getsockname()
            pt_qname = pt_address + "_" + str(pt_port)
            try:
                logger.debugv("Close Passtrough Connection socket: {0}".format(pt_socket))
                #Test if endpoint still exist?
                pt_socket.close()
            except Exception as e:
                logger.error("close Passthrough connection error:  %s", e)

        try:
            logger.debugv("clean Passthrough connection queues")
            if conf.serverpassthrough:
                if pt_qname in self.outputs:
                    del self.outputs[pt_qname]
                if pt_qname in self.inputs:
                    del self.inputs[pt_qname]
                if pt_qname in self.exceptional:
                    del self.exceptional[pt_qname]
                if s in self.lastmessage :
                    del self.lastmessage[pt_socket]
                del self.send_queuereg[pt_qname]

        except Exception as e:
            logger.debugv("clean connection queues error:  %s", e)

        #clean channels
        if s in self.channel:
                del self.channel[s]
        if conf.serverpassthrough:
            if pt_socket in self.channel:
                del self.channel[pt_socket]

    def waitsync(self,sequencenumber,sendersock,timeout=1):
        #this routine will wait on (ack/nack) response for a message
        #wil lbe crteadted in the future, no only perform a wait to give the remote (e.g. client or growatt server) the time to response.
        logger.debug("waitsync, wait for response on msg:{0} from: {1}".format(sequencenumber,sendersock))
        time.sleep(timeout)

    def process_data_record(self,conf,data):
        """this routine will process the growatt datarecords"""
        procdata(conf,data)
        logger.debug("process_data_record initiated")
        returncc = 0
        return(returncc)

    def process_data(self, conf, s, recInfo):

        #self.send_queuereg[qname].put(response)

        # Prevent generic errors:
        try:

            # process data and create response
            client_address, client_port = s.getpeername()
            qname = client_address + "_" + str(client_port)

            #V0.0.14: default response on record to none (ignore record)
            response = None

            # Display data
            logger.debug(f"Data received from : {client_address}:{client_port}")

            #validate data (Length + CRC for 05/06)
            #join gebeurt nu meerdere keren! Stroomlijnen!!!!
            vdata = "".join("{:02x}".format(n) for n in recInfo.origData)
            validatecc = validate_record(vdata)
            #validatecc = 0
            if validatecc != 0 :
                logger.info("process_data, invalid data record received, processing stopped for this record returncode: %s",validatecc)
                #Create response if needed?
                #self.send_queuereg[qname].put(response)
                return
            # Prepare response
            if recInfo.rectype in ("16"):
                # if ping send data as reply
                response = recInfo.origData
                logger.debug(f"process_data, 16- ping response:")
                logger.debug("\n{0}".format(format_multi_line("\t\t ", response)))

                #     #v0.0.14a: create temporary also logger record at ping (to support shinelink without inverters)

                loggerreg.update_logger(recInfo.loggerid, ip = client_address, port = client_port, protocol = recInfo.protocol)
                # logger.debug(f"process_data, datalogger id: {loggerid} added by ping: {loggerreg[loggerid]}")
            #v0.0.14: remove "29" (no response will be sent for this record!)
            elif recInfo.rectype in ("03", "04", "50", "1b", "20"):
                # if datarecord send ack.
                if recInfo.protocol == '02':
                    #protocol 02, unencrypted ack
                    response = bytes.fromhex(recInfo.header[0:8] + '0003' + recInfo.header[12:16] + '00')
                else:
                    # protocol 05/06, encrypted ack
                    headerackx = bytes.fromhex(recInfo.header[0:8] + '0003' + recInfo.header[12:16] + '47')
                    # Create CRC 16 Modbus
                    crc16 = calc_crc(headerackx)
                    # create response
                    response = headerackx + crc16.to_bytes(2, "big")
                
                logger.debug(f"Response: {format_multi_line('\t\t', response)}")

                loggerreg.update_logger(recInfo.loggerid, ip = client_address, port = client_port, protocol = recInfo.protocol  )   
                recInfo.setInverterID()
                loggerreg.add_inverter(recInfo.loggerid, recInfo.inverterid, recInfo.deviceid)

                if conf.mode=="server" :
                    procdatarc = self.process_data_record(conf,recInfo)
                    logger.debug("data record process ended with returncode: %s",procdatarc)

                if recInfo.rectype in ("03") :
                # init record register logger/inverter id (including sessionid?)
                # decrypt body.
                    
                    #send response
                    self.send_queuereg[qname].put(response)
                    #wait some time before response on announcement is processed (maybe create a waitsync routine?)
                    #self.waitsync(sequencenumber,s)
                    #time.sleep(5)
                    # Create time command en put on queue
                    response = createtimecommand(self,recInfo.protocol,recInfo.deviceid,recInfo.loggerid,"0001")
                    logger.debug("03 announce data record processed")

            elif recInfo.rectype in ("19","05","06","18"):
                logger.info(f"No response needed: Command Response record received {recInfo.infoStr()}" )

                offset = 0
                if recInfo.protocol in ("06") :
                    offset = 40

                register = int(recInfo.decryptedData[36+offset:40+offset],16)
                if recInfo.rectype == "05" :
                    #value = result_string[40+offset:44+offset]
                    #v0.0.14: test if empty response is sent (this will give CRC code as values)
                    #print("length resultstring:", len(result_string))
                    #print("result starts on:", 48+offset)
                    if len(recInfo.decryptedData) == 48+offset :
                        logger.debug("Grottserver - empty register get response recieved, response ignored")
                    else:
                        value = recInfo.decryptedData[44+offset:48+offset]
                elif recInfo.rectype == "06" :
                    result = recInfo.decryptedData[40+offset:42+offset]
                    #print("06 response result :", result)
                    value = recInfo.decryptedData[42+offset:46+offset]
                elif recInfo.rectype == "18" :
                    result = recInfo.decryptedData[40+offset:42+offset]
                    value = result # no value in 18 response, this is temporarily needed for new command response processing
                else :
                    # "19" response take length into account
                    valuelen = int(recInfo.decryptedData[40+offset:44+offset],16)
                    value = codecs.decode(recInfo.decryptedData[44+offset:44+offset+valuelen*2], "hex").decode('ISO-8859-1')

                regkey = "{:04x}".format(register)
                responseInfo = registerInfo(register,value)
                
                logger.info(f'Register {register} info regkey {regkey} : {responseInfo.value} recordInfo: {recInfo.infoStr()}')
                if recInfo.rectype == "06" :
                    loggerreg.update_inverter_register_response(recInfo.loggerid, recInfo.deviceid, register, value)
                elif recInfo.rectype == "18" :
                    loggerreg.update_datalogger_register_response(recInfo.loggerid, register, value)
                elif recInfo.rectype == "19" :
                    loggerreg.update_datalogger_register_response(recInfo.loggerid, register, value)
                else :
                    loggerreg.update_inverter_register_response(recInfo.loggerid, recInfo.deviceid, register, value)


                response = None

            elif recInfo.rectype in ("10") :
                logger.debug("Grottserver - " + recInfo.header[12:16] + " record received, no response needed")

                startregister = int(recInfo.decryptedData[76:80],16)
                endregister = int(recInfo.decryptedData[80:84],16)
                value = recInfo.decryptedData[84:86]

                regkey = "{:04x}".format(startregister) + "{:04x}".format(endregister)
                loggerreg.update_inverter_register_response(recInfo.loggerid, recInfo.deviceid, regkey, value)

                response = None

            elif recInfo.rectype in ("29") :
                logger.debug("Grottserver - " + recInfo.header[12:16] + " record received, no response needed")
                response = None

            #elif rectype in ("99") :
                #placeholder for communicating from html server to sendrecv server
            #    if verbose:
            #    response = None

            else:
                logger.debug("Grottserver - " + recInfo.header[12:16] + " unknown record received, no response")
                response = None

            if response is not None :
                #qname = client_address + "_" + str(client_port)
                logger.debug(f'Grottserver - Put response on queue: {qname} msg: {format_multi_line("    ", response)}')
                self.send_queuereg[qname].put(response)
        except Exception as e:
            print("\t - Grottserver - exception in main server thread occured : ", e)

def set_menu(section):
    
    menuconfig = {}
    
    if len(section)>0:
        menuconfig[section]="active"

    return menuconfig



def testFlaskCreateDummyData():
    #create some dummy data
    datalogger = loggerreg.add_logger("DLG001", ip = "1.1.1.1", port = 1234, protocol = "02")       
    loggerreg.add_inverter("DLG001","INV001","01")
    loggerreg.add_inverter("DLG001","INV002","02")
    datalogger = loggerreg.add_logger("DLG002", ip = "2.2.2.2", port = 2345, protocol = "05") 
    loggerreg.add_inverter("DLG002","INV003","01")


from flask import Flask, request, jsonify, render_template, make_response, redirect, url_for
from flask.views import MethodView
from flask_wtf import FlaskForm
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from wtforms import StringField, SubmitField, SelectField, PasswordField
from wtforms.validators import DataRequired, Length
from werkzeug.security import generate_password_hash, check_password_hash
import platform

class User(UserMixin):
    """User class for Flask-Login"""
    def __init__(self, id, username, password_hash):
        self.id = id
        self.username = username
        self.password_hash = password_hash

class LoginForm(FlaskForm):
    """Login form with username and password"""
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=64)])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=1, max=128)])
    submit = SubmitField('Login')

class RegisterValueForm(FlaskForm):
    targetSelect = SelectField('Target (inverter/datalogger)', choices=[], validators=[DataRequired()])
    start = StringField('Start Register', validators=[DataRequired(), Length(min=1, max=10)])
    end = StringField('End Register', validators=[DataRequired(), Length(min=1, max=10)])
    value = StringField('Register Value')  # This field can be used to display the fetched value   
    getValue = SubmitField('Get Register Value')
    setValue = SubmitField('Set Register Value')

def when_ready(server):
    # Called just after the server is started
    logger.info("Gunicorn server is ready. Starting other services...")
    # Start background servers here for Linux
    background_threads = server.start_background_servers()

class FlaskServer():
    def __init__(self, conf, httphost, httpport, send_queuereg):
        self.app = Flask(__name__)
        self.app.config['SECRET_KEY'] = 'b248c447afda729f12954257ab22777af9c0699899348e6dc44810cf980176ef'
        self.app.config['TEMPLATES_AUTO_RELOAD'] = True

        self.httphost = httphost
        self.httpport = httpport    
        self.send_queuereg = send_queuereg
        self.conf = conf
        
        # Initialize Flask-Login
        self.login_manager = LoginManager()
        self.login_manager.init_app(self.app)
        self.login_manager.login_view = 'login'
        self.login_manager.login_message = 'Please log in to access this page.'
        
        # In-memory user store (can be extended with database)
        # Default credentials: username=admin, password=admin
        self.users = {
            '1': User('1', 'admin', generate_password_hash('admin'))
        }
        
        # Load user callback
        @self.login_manager.user_loader
        def load_user(user_id):
            return self.users.get(user_id)
        
        # Register routes
        self.app.add_url_rule('/login', view_func=self.Login.as_view('login', server=self))
        self.app.add_url_rule('/logout', view_func=self.Logout.as_view('logout', server=self))
        self.app.add_url_rule('/', view_func=self.Home.as_view('home', server=self))
        self.app.add_url_rule('/registerOverview', view_func=self.RegisterOverview.as_view('registerOverview', server=self))
        self.app.add_url_rule('/register', view_func=self.Register.as_view('register', server=self))
        # API endpoint for register overview used by Grid.js server mode
        self.app.add_url_rule('/api/registers', view_func=self._api_registers, methods=['GET'])
        # Backward compatibility: port GrottHttpServer endpoints to Flask
        # Provide same endpoints and behavior as the original GrottHttpRequestHandler
        self.app.add_url_rule('/info', view_func=self._info, methods=['GET'])
        self.app.add_url_rule('/help', view_func=self._help, methods=['GET'])
        # datalogger and inverter endpoints accept both GET and PUT similar to GrottHttpServer
        self.app.add_url_rule('/datalogger', view_func=self._datainv, methods=['GET','PUT'])
        self.app.add_url_rule('/inverter', view_func=self._datainv, methods=['GET','PUT'])

        # Ensure templates always receive `mc` (menu config) via a context processor.
        @self.app.context_processor
        def inject_mc():
            # Try to derive a logical section name from the endpoint or path.
            section = ''
            try:
                endpoint = request.endpoint or ''
            except Exception:
                endpoint = ''

            # Map endpoints to menu sections used by set_menu()
            endpoint_map = {
                'home': 'home',
                'registerOverview': 'registerOverview',
                'register': 'register',
            }

            section = endpoint_map.get(endpoint, '')

            # Provide `mc` to templates and keep compatibility if set_menu raises
            try:
                return {'mc': set_menu(section)}
            except Exception:
                return {'mc': {}}

    def run(self):
        if conf.waitressServer:
            from waitress import serve
            serve(self.app, host=self.httphost, port=self.httpport, threads=2)
        else:
            # For Gunicorn server, is not working at the moment
            from gunicorn.app.base import BaseApplication

            class GunicornApp(BaseApplication):
                def __init__(self, app, options=None):
                    self.options = options or {}
                    self.application = app
                    super().__init__()

                def load_config(self):
                    for key, value in self.options.items():
                        self.cfg.set(key.lower(), value)

                def load(self):
                    return self.application


            options = {
                'bind': f'{self.httphost}:{self.httpport}',
                'workers': 1,
                'worker_class': 'sync',
                'timeout': 600,
                'keepalive': 5,
                'graceful_timeout': 30,
                'preload_app': True,
                'when_ready': when_ready,
                'worker_connections': 100
            }
            self.server = Server(self.conf)  # Store server instance
            GunicornApp(self.app, options).run()
    
    class Login(MethodView):
        def __init__(self, server):
            self.server = server
        
        def get(self):
            if current_user.is_authenticated:
                return redirect(url_for('home'))
            form = LoginForm()
            return render_template('login.html', form=form)
        
        def post(self):
            form = LoginForm()
            if form.validate_on_submit():
                username = form.username.data
                password = form.password.data
                
                # Check credentials
                user = None
                for uid, u in self.server.users.items():
                    if u.username == username and check_password_hash(u.password_hash, password):
                        user = u
                        break
                
                if user:
                    login_user(user, remember=True)
                    logger.info(f"User {username} logged in successfully")
                    next_page = request.args.get('next')
                    return redirect(next_page) if next_page else redirect(url_for('home'))
                else:
                    logger.warning(f"Failed login attempt for username: {username}")
                    form.errors['login'] = ['Invalid username or password']
            
            return render_template('login.html', form=form)
    
    class Logout(MethodView):
        def __init__(self, server):
            self.server = server

        def get(self):
            username = current_user.username if current_user.is_authenticated else 'Unknown'
            logout_user()
            logger.info(f"User {username} logged out")
            return redirect(url_for('login'))
    
    def fillTargetChoices(self, choices):
        dataloggers = loggerreg.loggers.values()
        for datalogger in dataloggers:
            name =  datalogger.dataloggerid
            choices.append((name, f"Datalogger : {name}"))   
            for inverterid in datalogger.inverters.keys():
                choices.append((inverterid, f"Inverter : {inverterid} of Datalogger : {datalogger.dataloggerid}"))
            
    class Register(MethodView):
        def __init__(self, server):
            self.server = server
#            testFlaskCreateDummyData()

        @login_required
        def get(self):
            form = RegisterValueForm()
            self.server.fillTargetChoices(form.targetSelect.choices)
            return render_template('register.html',  mc=set_menu('register'), loggerreg=loggerreg, form=form)

        @login_required
        def post(self):
            form = RegisterValueForm()
            self.server.fillTargetChoices(form.targetSelect.choices)
            if form.validate_on_submit():
                name = form.targetSelect.data
                if form.getValue.data:
                    # Get the current value of the register
                    regInfo = queueAndGetRegisterValue(self.server.send_queuereg, name, readCommand=True, startregister=form.start.data, endregister=form.end.data)
                    form.value.data = regInfo.value
                    logger.info(f"Register value retrieved for datalogger or inverter {name} register {form.start.data} : {regInfo.value}")
                elif form.setValue.data:
                    # Set the value of the register
                    logger.info(f"Form submitted to set value with target: {name}, start: {form.start.data}, end: {form.end.data}, value: {form.value.data}")
                    regInfo = queueAndGetRegisterValue(self.server.send_queuereg, name, readCommand=False, startregister=form.start.data, value=form.value.data)
            return render_template('register.html', mc=set_menu('register'), loggerreg=loggerreg, form=form)

    class RegisterOverview(MethodView):
        def __init__(self, server):
            self.server = server

        @login_required
        def get(self):
            return render_template('registerOverview.html',  mc=set_menu('registerOverview'), loggerreg=loggerreg)


    class About(MethodView):
        def get(self):
            return render_template('about.html', mc=set_menu('home'))

    
    class Home(MethodView):
        def __init__(self, server):
            self.server = server

        @login_required
        def get(self):
            queueinfo = []
            for key, value in self.server.send_queuereg.items():
                queueinfo.append((key, value.qsize()))
            queueinfo.sort(key=lambda x: x[0])
            current_level = logging.getLevelName(logger.level)
            return render_template('home.html', mc=set_menu('home'), threadCount = threading.active_count(),
                                   memory=psutil.Process(os.getpid()).memory_info().rss/1024**2, 
                                   activeThreads=threading.enumerate(), current_level=current_level,
                                   queueinfo=queueinfo, loggerreg=loggerreg)
        
        @login_required
        def post(self):
            # Get the selected log level from the form
            action = request.form.get('action')
            log_level = request.form.get('log_level', 'INFO').upper()
            if log_level in [ 'DEBUGV','DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']:
                logger.setLevel(getattr(logging, log_level))
                logger.info(f"Log level changed to {log_level}")
            else:
                logger.warning("Invalid log level selected")
            return self.get()


    class RegisterAPI(MethodView):
        def __init__(self, server):
            self.server = server

        def get(self):
            register_start = request.args.get('start', default=0, type=int)
            register_end = request.args.get('end', default=-1, type=int)
            target = request.args.get('target', default='', type=str)
            name = request.args.get('name', default='', type=str)

            regInfo = queueAndGetRegisterValue(self.server.send_queuereg, name, readCommand=True, startregister=register_start)
            return jsonify({'value': regInfo.value})
            
    # --- Ported handlers from GrottHttpRequestHandler for backward compatibility ---
    def _info(self):
        # emulate original info endpoint
        try:
            logger.info("FlaskServer - Status requested via /info")
            # Log some runtime info to allow operator inspection in logs
            logger.info(" - Grottserver #active threads count: %s", threading.active_count())
            try:
                import os as _os, psutil as _psutil
                logger.info(" - Grottserver memory in use : %s MB", _psutil.Process(_os.getpid()).memory_info().rss/1024**2)
            except Exception:
                logger.info(" - Grottserver PSUTIL not available no process information can be printed")

            logger.info(" - Grottserver connection queue : %s", list(self.send_queuereg.keys()))
            return make_response("<h2>Grottserver info generated, see log for details</h2>", 200)
        except Exception as e:
            logger.warning("Exception in /info: %s", e)
            return make_response("Internal Server Error", 500)

    def _help(self):
        return make_response(b'No help available yet', 200)

    def _api_registers(self):
        """
        Return paginated register overview JSON for Grid.js server mode with server-side sorting and filtering.
        Response includes metadata fields: name, description, unit, readOnStart.
        Response format: {"data": [ {idType, id, regno, name, description, unit, readOnStart, retrievalDateIso, retrievalDateFmt, value}, ... ], "total": <int> }
        Query params supported: 
          - page (1-based, default 1), perPage (default 25)
          - search (global search across all columns, case-insensitive)
          - sortBy (column id: idType, id, regno, retrievalDate, value, name)
          - sortDir (asc or desc, default asc)
          - filterIdType (filter by idType: Datalogger, Inverter, or empty for all)
        """
        try:
            page = request.args.get('page', default=1, type=int)
            per_page = request.args.get('perPage', default=25, type=int)
            search = request.args.get('search', default='', type=str).lower()
            sort_by = request.args.get('sortBy', default='id', type=str)
            sort_dir = request.args.get('sortDir', default='asc', type=str).lower()
            filter_id_type = request.args.get('filterIdType', default='', type=str)

            items = []
            # dataloggers
            for dataloggerid, datalogger in loggerreg.loggers.items():
                datalogger.createRegOverview(items)
                
            # inverters
            for inverterid, inverter in loggerreg.inverters.items():
                inverter.createRegOverview(items)
            # Now items is a list of dicts with all register overview entries

            # apply idType filter
            if filter_id_type:
                items = [it for it in items if it['idType'] == filter_id_type]

            # filter by global search
            if search:
                def matches(it):
                    try:
                        return (search in str(it['id']).lower() or
                                search in str(it['regno']).lower() or
                                search in str(it['value']).lower() or
                                search in str(it['name'] or '').lower() or
                                search in str(it['description'] or '').lower() or
                                search in str(it['idType']).lower())
                    except Exception:
                        return False
                items = [it for it in items if matches(it)]

            total = len(items)

            # server-side sorting
            def sort_key(item):
                val = item.get(sort_by)
                if sort_by == 'regno':
                    try:
                        return (int(val), )
                    except Exception:
                        return (0, )
                elif sort_by == 'retrievalDate':
                    # sort by datetime object directly
                    return (val if val else datetime.min, )
                else:
                    # string sort for idType, id, value, name, description
                    return (str(val).lower() if val else '', )
            
            reverse = (sort_dir == 'desc')
            items.sort(key=sort_key, reverse=reverse)

            # pagination (page is 1-based)
            start = max((page - 1) * per_page, 0)
            end = start + per_page
            if end > total:
                end = total
            page_items = items[start:end]
            logger.info(f"/api/registers - page {page} per Page {per_page} total items after filtering: {total}, returning items {start} to {end}")

            # format retrievalDate to both iso and human-readable strings
            for it in page_items:
                rd = it.get('retrievalDate')
                try:
                    it['retrievalDateIso'] = rd.isoformat() if hasattr(rd, 'isoformat') else str(rd)
                    # human-readable format: "2025-11-20 14:30:45"
                    if hasattr(rd, 'strftime'):
                        it['retrievalDateFmt'] = rd.strftime('%Y-%m-%d %H:%M:%S')
                    else:
                        it['retrievalDateFmt'] = str(rd)
                except Exception:
                    it['retrievalDateIso'] = str(rd)
                    it['retrievalDateFmt'] = str(rd)

            return jsonify({'data': page_items, 'total': total})
        except Exception as e:
            logger.exception('Exception in /api/registers: %s', e)
            return make_response(b'Internal Server Error', 500)

    def _datainv(self):
        # Combined handler for /datalogger and /inverter. Determine which based on path.
        try:
            path = request.path.lstrip('/')
            is_datalogger = path == 'datalogger'

            if request.method == 'GET':
                # map to original logic for GET
                if is_datalogger:
                    sendcommand = CommandType.DataLoggerReadRegisters
                    logger.debug("FlaskServer - datalogger GET received: %s", request.args)
                else:
                    sendcommand = CommandType.InverterReadRegisters
                    logger.debug("FlaskServer - inverter GET received: %s", request.args)

                if not request.args:
                    # no args => return logger registry info (JSON representation)
                    try:
                        data = json.dumps({k: v.__dict__ for k, v in loggerreg.loggers.items()}).encode('ISO-8859-1')
                    except Exception:
                        data = json.dumps(list(loggerreg.loggers.keys())).encode('ISO-8859-1')
                    return make_response(data, 200)

                # validate command
                command = request.args.get('command')
                if not command or command not in ("register", "regall"):
                    return make_response(b'no valid command entered', 400)

                # get datalogger/inverter target
                datalogger = None
                if sendcommand == CommandType.InverterReadRegisters:
                    inverterid = request.args.get('inverter')
                    if inverterid:
                        datalogger = loggerreg.find_datalogger_by_inverter(inverterid)
                    if not datalogger:
                        return make_response(b'no or no valid invertid specified', 400)
                    formatval = request.args.get('format', 'dec')
                    if formatval not in ("dec", "hex", "text"):
                        return make_response(b'invalid format specified', 400)
                else:
                    try:
                        dataloggerid = request.args.get('datalogger')
                        datalogger = loggerreg[dataloggerid]
                    except Exception:
                        return make_response(b'invalid datalogger id', 400)

                if command == 'regall':
                    # return all register values for this target
                    comresp = {}
                    if is_datalogger:
                        for regkey, reginfo in datalogger.registers.items():
                            comresp[regkey] = reginfo.value
                    else:
                        inverter = datalogger.inverters.get(inverterid)
                        if not inverter:
                            return make_response(b'invalid inverter id', 400)
                        for regkey, reginfo in inverter.registers.items():
                            comresp[regkey] = reginfo.value
                    return make_response(json.dumps(comresp).encode('ISO-8859-1'), 200)

                # command == 'register' - use new queue-based flow
                register = request.args.get('register')
                try:
                    if int(register) < 0 or int(register) >= 4096:
                        return make_response(b'invalid reg value specified', 400)
                except Exception:
                    return make_response(b'invalid reg value specified', 400)

                try:
                    # Use new queueAndGetRegisterValue flow
                    if is_datalogger:
                        name_for_cmd = datalogger.dataloggerid
                    else:
                        name_for_cmd = inverterid
                    regInfo = queueAndGetRegisterValue(self.send_queuereg, name_for_cmd, readCommand=True, startregister=int(register))
                    
                    # Format value if needed
                    if sendcommand == CommandType.InverterReadRegisters:
                        if formatval == 'dec':
                            regInfo.value = int(regInfo.value, 16)
                        elif formatval == 'text':
                            regInfo.value = codecs.decode(regInfo.value, 'hex').decode('ISO-8859-1')
                    
                    response_dict = {'value': regInfo.value}
                    return make_response(json.dumps(response_dict).encode('ISO-8859-1'), 200)
                except Exception as e:
                    logger.warning("Exception in GET register handler: %s", e)
                    return make_response(b'no or invalid response received', 400)

            elif request.method == 'PUT':
                # Ported PUT logic using new queue-based flow
                if is_datalogger:
                    sendcommand = CommandType.DataLoggerWriteRegisters
                    logger.debug("FlaskServer - datalogger PUT received: %s", request.args)
                else:
                    sendcommand = CommandType.InverterWriteSingleRegister
                    logger.debug("FlaskServer - inverter PUT received: %s", request.args)

                command = request.args.get('command')
                if not command or command not in ("register", "multiregister", "datetime"):
                    return make_response(b'no valid command entered', 400)

                # find datalogger
                datalogger = None
                if sendcommand == CommandType.InverterWriteSingleRegister:
                    inverterid = request.args.get('inverter')
                    if inverterid:
                        datalogger = loggerreg.find_datalogger_by_inverter(inverterid)
                    if not datalogger:
                        return make_response(b'no or invalid invertid specified', 400)
                    formatval = request.args.get('format', 'dec')
                else:
                    try:
                        dataloggerid = request.args.get('datalogger')
                        datalogger = loggerreg[dataloggerid]
                    except Exception:
                        return make_response(b'invalid datalogger id', 400)

                try:
                    # validate and prepare values based on command type
                    if command == 'register':
                        register = request.args.get('register')
                        value = request.args.get('value')
                        try:
                            if int(register) < 0 or int(register) >= 4096:
                                return make_response(b'invalid reg value specified', 400)
                        except Exception:
                            return make_response(b'invalid reg value specified', 400)
                        if value is None or value == '':
                            return make_response(b'no value specified', 400)
                        
                        # Convert value format for inverter command if needed
                        if sendcommand == CommandType.InverterWriteSingleRegister:
                            if formatval == 'dec':
                                value = int(value)
                            elif formatval == 'text':
                                value = int(value.encode('ISO-8859-1').hex(), 16)
                            elif formatval == 'hex':
                                value = int(value, 16)
                            # Validate range for 16-bit value
                            if value < 0 or value > 65535:
                                return make_response(b'invalid value specified', 400)
                            value = int(value)
                        
                        # Use new queue-based flow for single register write
                        # build commandInfo for this target
                        if is_datalogger:
                            name_for_cmd = datalogger.dataloggerid
                        else:
                            name_for_cmd = inverterid
                        cmdInfo = commandInfo(name_for_cmd, False)
                        queueRegisterCommand(self.send_queuereg, cmdInfo, startregister=int(register), value=value)
                        return make_response(b'OK', 200)
                    
                    elif command == 'multiregister':
                        try:
                            startregister = int(request.args.get('startregister'))
                            endregister = int(request.args.get('endregister'))
                        except Exception:
                            return make_response(b'invalid start/end register value specified', 400)
                        value = request.args.get('value')
                        if not value:
                            return make_response(b'no value specified', 400)
                        
                        # Use new queue-based flow for multiregister write
                        if is_datalogger:
                            name_for_cmd = datalogger.dataloggerid
                        else:
                            name_for_cmd = inverterid
                        cmdInfo = commandInfo(name_for_cmd, False)
                        queueRegisterCommand(self.send_queuereg, cmdInfo, 
                                           startregister=startregister, endregister=endregister, value=value)
                        return make_response(b'OK', 200)
                    
                    elif command == 'datetime':
                        if sendcommand == CommandType.InverterWriteSingleRegister:
                            return make_response(b'datetime command not allowed for inverter', 400)
                        
                        # Use new queue-based flow for datetime write
                        if is_datalogger:
                            name_for_cmd = datalogger.dataloggerid
                        else:
                            name_for_cmd = inverterid
                        cmdInfo = commandInfo(name_for_cmd, False)
                        queueRegisterCommand(self.send_queuereg, cmdInfo, register=31, value=str(datetime.now().replace(microsecond=0)))
                        return make_response(b'OK', 200)
                    
                    else:
                        return make_response(b'command not defined or not available yet', 400)
                
                except Exception as e:
                    logger.warning("Exception in PUT handler: %s", e)
                    return make_response(b'no or invalid response received', 400)

        except Exception as e:
            logger.exception('Exception in datalogger/inverter handler: %s', e)
            return make_response(b'Internal Server Error', 500)

class Server :
    def __init__(self, conf):
        #set loglevel
        logger.setLevel(conf.loglevel.upper())
        conf.vrmserver = vrmserver
        logger.info("Grottserver inititialisation started, grottserver version %s",conf.vrmserver)

    def main(self,conf):
        #set loglevel
        self.conf=conf
        logger.setLevel(conf.loglevel.upper())
        logger.info("Grott server started")
        logger.info("mode: %s",conf.mode)
        send_queuereg = {}

        # GrottHttpServer replaced by FlaskServer
        # http_server = GrottHttpServer(conf, conf.serverip, conf.httpport, send_queuereg)
        flask_server = FlaskServer(conf, conf.serverip, conf.httpport, send_queuereg)
        #connection_server = sendrecvserver(conf.serverip, conf.serverport, send_queuereg)
        connection_server = sendrecvserver(conf, conf.serverip, conf.serverport, send_queuereg)
        servername = "conserver_" + conf.serverip + ":" + str(conf.serverport)
        def start_background_servers():
            connection_server_thread = threading.Thread(target=connection_server.run, name=servername, args=[conf])
            # http_server_thread = threading.Thread(target=http_server.run, name=httpname, args=[conf])
            
            # Make other threads daemon so they exit when main thread exits
            connection_server_thread.daemon = True
            # http_server_thread.daemon = True
            
            # Start background threads
            # http_server_thread.start()
            connection_server_thread.start()
            return [connection_server_thread]  # http_server_thread removed (replaced by Flask)

        # Run Flask/Gunicorn in main thread
        try:
#            if platform.system() == 'Windows':
            if conf.waitressServer:               # On Waitress, start background servers before running Flask
                background_threads = start_background_servers()
                flask_server.run()
            else: #gunicorn implementation not correct, call back is not working
                # For Linux, we'll start the background servers in Gunicorn's when_ready callback
                flask_server.conf = conf  # Pass conf to flask server
                flask_server.start_background_servers = start_background_servers  # Pass the function
                flask_server.run()
        except KeyboardInterrupt:
            logger.info("Shutting down server...")
        finally:
            logger.info("Server shutdown complete")

if __name__ == "__main__":
    """main module: New entry for people running the new combined grottserver make grott.py obsolete"""
    addLoggingLevel("DEBUGV", logging.DEBUG - 5)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s() - %(message)s")
    logger.info("Grottserver Version: %s",vrmserver)
    logger.info("Grottserver will run in combined mode")
    try:
        # process config file:
        confserver = True
        conf = Conf(vrmserver)
        logger.debug("Configuration being set by grottconf")
        #change loglevel might be changed after config processing.
        logger.setLevel(conf.loglevel.upper())
    except Exception as e:
        logger.error(f"Unable to load config: {e}")
        exit()

    server = Server(conf)
    try:
        server.main(conf)
    except KeyboardInterrupt:
        logger.info("Ctrl C - Stopping server")
        exit()

        # try:
        #     logger.info("closeport")
        #     #proxy.on_close(conf)
        # except:
        #     logger.info("\t - no ports to close")
