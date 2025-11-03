"""Grottconf setting the configuration environment (class Conf)"""
# grottconf  process command parameter and settings file
# Updated: 2024-12-08
import configparser, sys, argparse, os, json, io
import ipaddress
from os import walk
from grottdata import format_multi_line, str2bool
import logging
#set logging definities
#logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
vrmconf = "3.2.0_20250521"

class Conf :
    """define/proces grott configuration settings"""
    def __init__(self, vrm):
        """"Init Configuration"""
        logger.info("Config Processing Started")
        #Set parm's
        #prio: 1.Command line parms, 2.env. variables, 3.config file 4.program default
        #process command settings that set processing values (verbose, trace, output, config, nomqtt)
        # Set default for the command line parms
        self.verrel = vrm
        self.vrmconf = vrmconf
        self.vrmproxy = "3.0.0_241019"
        self.vrmsniff = "1.1.2"
        self.vrmdata = "3.2.0_20250521"
        self.vrmserver = "3.2.0_20250521"
        self.verbose= False
        self.loglevel = "INFO"
        self.cfgfile = "grott.ini"
        self.parserinit()
        #Set default config:
        self.defaultconf()
        #Process config file
        self.procconf()
        #Process environmental variable
        self.procenv()
        #Process variables to override/correct  config and environmental settings
        self.confpost()
        self.resetdebuglevel()
        #print configuration
        self.print()
        #test print only info
        #self.print(["Info","Hardcoded"])

        #process not if in standalone mode
        if self.mode not in ["serversa"]:
            #prepare MQTT security
            if not self.mqttauth: self.pubauth = None
            else: self.pubauth = dict(username=self.mqttuser, password=self.mqttpsw)

            #define recordlayouts
            self.set_reclayouts()

            #define record whitlist (if blocking / filtering enabled
            self.set_recwl()

            #prepare influxDB
            if self.influx :
                returncc=self.procinflux()
                if returncc[0] == 0 : logger.info(returncc[1])
                elif returncc[0] > 4 :
                    logger.critical(returncc[1])
                    raise SystemExit()
                else :
                    self.influx = False
                    logger.warning("%s, Grott procesing will continue without InfluxDB", returncc[1])
                    #logger.warning(returncc[1] + ", Grott procesing will continue without InfluxDB")

    def defaultconf(self) :
        """set config defaults"""
        #define parameters dictionary :
        #format self.parm[parm] = {"type" : parmtype,"value": parmvalue, "environ" : environ_var/none, "show" : show,noshow]
        #use addparm(self,type,parm,value,environ=None,show=True) :
        self.parm = dict()
        #define parmtype / secyions in parmlib be aware hardcoded will be ignorded from parmlib
        self.parmtype = ["Info","Hardcoded","Generic","Growatt","Server","MQTT","PVOutput","influx","extension"]
        ###Set fixed variables (not changable with .ini or environmental variables)
        self.addparm("Info","verrel",self.verrel)
        self.addparm("Info","verrelconf",vrmconf)
        self.addparm("Info","verreldata",self.vrmdata)
        self.addparm("Info","verrelproxy",self.vrmproxy)
        self.addparm("Info","verrelsniff",self.vrmsniff)
        self.addparm("Info","verrelserver",self.vrmserver)
        #set changeable variables (in .ini or environmentals:
        #recordtypes for inverter data processing
        self.addparm("Hardcoded","datarec",["04","50"])
        #recordtypes for inverter data processing
        self.addparm("Hardcoded","smartmeterrec",["1b","20","1e"])
        #!depricated! minimal datarecord length that can be processed (no ack record!)
        self.addparm("Hardcoded","mindatarec",12)
         #3.0.0. invertid not changable anymore, only there for compatability
        self.addparm("Hardcoded","inverterid","automatic")
        #set standard sysout, can only be overwritten at startup
        self.addparm("Hardcoded","outfile","sys.stdout")
        ###Set default variables
        #3.0.0 not recommended use loglevel!
        self.addparm("Generic","verbose",self.verbose,"gverbose")
        #Standard python logging level: DEBUG,INFO,WARNING,ERROR,CRITICAL added DEBUGV (debug verbose+)
        self.addparm("Generic","loglevel",self.loglevel,"gloglevel")
        #config file can only be set via startup parameter
        self.addparm("Generic","cfgfile",self.cfgfile)
        #specify lower minrecl (e.g. minrecl = 1) to log / debug all records. minrecl = 100 will supress most of commincation records except data related records
        self.addparm("Generic","minrecl",100,"gminrecl")
        #specify invertype:  default (use standard tl-s type of inverters, automatic (>3.0.0, lets grott automatically detect),spf, sph, mod etc use specific invertypes)
        self.addparm("Generic","invtype","auto","ginvtype")
        #define invertype setting for multiple servers {"invertid" : "invtype", "invertid1" : "invtype1", }
        self.addparm("Generic","invtypemap","{}","ginvtypemap")
        #Include all defined keys from layout (also incl = no)
        self.addparm("Generic","includeall","False","gincludeall")
        #Block growatt inverter and Shine configure commands
        self.addparm("Generic","blockcmd","False","gblockcmd")
        #Allow IP change if needed (not recommend setting, only use this for short time)
        self.addparm("Generic","noipf",False,"gnoipf")
        #time used =  auto: use record time or if not valid server time, alternative server: use always server time 3.0.0 renamed conf.gtime to conf.time gtime set for compat reasons.
        self.addparm("Generic","gtime","auto","gtime")
        self.addparm("Generic","time","auto","gtime")
        # enable / disable sending historical data from buffer
        self.addparm("Generic","sendbuf",True,"gsendbuf")
        #set defaultmode
        self.addparm("Generic","mode","proxy","gmode")
        #set default grott port
        self.addparm("Generic","grottport",5279,"grottport")
        #set default grott ip
        self.addparm("Generic","grottip","default","ggrottip")
        #set timezone (at this moment only used for influxdb)
        self.addparm("Generic","tmzone","local","gtmzone")
        ### Growatt settings
        # self.growattip = "server.growatt.com"
        # For China:                     server-cn.growatt.com
        # For US:                        server-us.growatt.com
        # For Australia and New Zealand: server-au.growatt.com
        self.addparm("Growatt","growattip","server.growatt.com", "ggrowattip")
        self.addparm("Growatt","growattport",5279,"ggrowattport")
        ### Grottserver settings
        self.addparm("Server","serverip","0.0.0.0","gserverip")
        self.addparm("Server","serverport",5781,"gserverport")
        # pass data to growatt
        self.addparm("Server","serverpassthrough",False,"gserverpassthrough")
        #httpserver
        self.addparm("Server","httpport",5782,"ghttpport")
        #Time to sleep waiting on API response
        self.addparm("Server","apirespwait",0.5,"gapirespwait")
        #Totaal time in seconds to wait on Inverter Response
        self.addparm("Server","inverterrespwait",10,"ginverterrespwait")
        #Totaal time in seconds to wait on Datalogger Response
        self.addparm("Server","dataloggerrespwait",5,"gdataloggerrespwait")
        #Totaal time in seconds to wait before a inactive session will be closed
        self.addparm("Server","ConnectionTimeout",200,"gConnectionTimeout")

        #MQTT Basic settings
        ##self.nomqtt = False
        self.addparm("MQTT","nomqtt",False,"gnomqtt")
        ##self.mqttip = "localhost"
        self.addparm("MQTT","mqttip","localhost","gmqttip")
        ##self.mqttport = 1883
        self.addparm("MQTT","mqttport",1883,"gmqttport")
        ##self.mqtttopic= "energy/growatt"
        self.addparm("MQTT","mqtttopic","energy/growatt","gmqtttopic")
        ##self.mqttretain = False
        self.addparm("MQTT","mqttretain",False,"gmqttretain")
        #MQTT Security Settings
        ##self.mqttauth = False
        self.addparm("MQTT","mqttauth",False,"mqttauth")
        ##self.mqttuser = "grott"
        self.addparm("MQTT","mqttuser","grott","gmqttuser")
        ##self.mqttpsw = "growatt2020"
        self.addparm("MQTT","mqttpsw","growatt2020","gmqttpsw","noshow")
        #MQTT Advanced Settings
        ##self.mqttmtopic = "False"
        self.addparm("MQTT","mqttmtopic",False,"gmqttmtopic")
        ##self.mqttmtopicname= "energy/meter"
        self.addparm("MQTT","mqttmtopicname","energy/meter","gmqttmtopicname")
        #self.mqttinverterintopic = False
        self.addparm("MQTT","mqttinverterintopic",False,"gmqttinverterintopic")

        #pvoutput default
        self.pvoutput = False
        self.pvinverters = 1
        self.pvurl = "https://pvoutput.org/service/r2/addstatus.jsp"
        self.pvapikey = "yourapikey"
        self.pvsystemid = {}
        self.pvinverterid = {}
        self.pvsystemid[1] = "systemid1"
        self.pvinverterid[1] = "inverter1"
        self.pvdisv1 = False
        self.pvtemp = False
        self.pvuplimit = 5

        #influxdb default
        self.influx = False
        self.influx2 = False
        self.ifdbname = "grottdb"
        self.ifip = "localhost"
        self.ifport = 8086
        self.ifuser = "grott"
        self.ifpsw  = "growatt2020"
        self.iftoken  = "influx_token"
        self.iforg  = "grottorg"
        self.ifbucket = "grottdb"

        #extension
        #self.extension = False
        self.addparm("extension","extension",False,"gextension")
        #self.extname = "grottext"
        self.addparm("extension","extname","grottext","gextname")
        #self.extvar = {"ip": "localhost", "port":8000}
        self.addparm("extension","extvar",'{"none": "none"}',"gextvar")


    def resetdebuglevel(self):
        #Reset loggerlevel if changed during parm processing
        #verbose == True if loglevel == "debug" (For compat reason, till all messages are displayed via logger)
        #if self.loglevel.upper() == "DEBUG" : self.verbose = True
        if self.loglevel.upper() in ("DEBUG","DEBUGV") : self.changeparm("verbose",True)
        elif self.verbose == True : self.changeparm("loglevel","DEBUG")
        logger.setLevel(self.loglevel.upper())

    def addparm(self,type,parm,value,environ=None,show="show") :
        #nieuw parameter format conf.parm[parm] = {"type" : parmtype,"value": parmvalue, "environ" : environ_var/none, "show" : True/False}]
        self.parm[parm] = {"type" : type,"value": value, "environ" : environ, "show" : show}
        logger.debug("config parameter added: {0} = {1}".format(parm,self.parm[parm]))
        #set conf.parm
        setattr(self, parm, value)

    def changeparm(self,parm,value) :
        #set new parameter value
        self.parm[parm]["value"] = value
        logger.debug("config parameter changed: {0} = {1}".format(parm,self.parm[parm]))
        #change conf.parm
        setattr(self, parm, value)

    def print(self,list="all"):
        """Print configuration settins"""
        logger.info("Grottsettings:\n")
        #logger.info("_Generic:")
        if list == "all" :
            printlist = self.parmtype
            if self.mode in ["proxy","sniffer"]:
                printlist.remove("Server")
            if self.mode in ["serversa"] :
                for item in ("MQTT","PVOutput","influx","extension"):
                    try:
                        printlist.remove(item)
                    except Exception as e:
                        logger.debug("item already deleted: %s",item)
        else:
            printlist = list

        #for parmtype in self.parmtype:
        for parmtype in printlist:
            logger.info(f"_{parmtype}:")
            # parmname = "self."key
            # exec(f"{[parmvalue]} = {parmname}")
            for key in self.parm :
                if self.parm[key]["environ"] == None : self.parm[key]["environ"] =""
                #if self.parm[key]["type"] == parmtype and self.parm[key]["show"]:
                if self.parm[key]["type"] == parmtype :
                    #get real value
                    value = getattr(self,key)
                    if self.parm[key]["show"] == "show" :
                        logger.info(("\t{0:<20}{1}{2:<21}{3}".format(key,"",self.parm[key]["environ"],value)))
                    else:
                        logger.info(("\t{0:<20}{1}{2:<21}{3}".format(key,"",self.parm[key]["environ"],"**secret**")))
        # print("oud")
        #logger.info("\t\tVersion:{0:>20}{1:<30}".format("",self.verrel))
        #logger.info("\t\tVersion:{0:>20}{1:<30}".format("",self.parm["generic"]["verrel"]))
        # print(self.verbose)
        # logger.info("\t\tverbose:{0:>20}{1:<30}".format("",["False","True"][self.verbose]))
        # logger.info("\t\tloglevel:\t\t%s",self.loglevel)
        #logger.info("\t\ttrace:  \t\t%s",self.trace)
        # logger.info("\t\tconfigfile:\t\t%s",self.cfgfile)
        # logger.info("\t\tminrecl:\t\t%s",self.minrecl)
        #logger.info("\tdecrypt:\t\t%s",self.decrypt)
        #logger.info("\tcompat:\t\t%s",self.compat)
        # logger.info("\t\tinvtype:\t\t%s",self.invtype)
        # logger.info("\t\tinvtypemap:\t\t%s",self.invtypemap)
        # logger.info("\t\tinclude_all:\t\t%s",self.includeall)
        # logger.info("\t\tblockcmd:\t\t%s",self.blockcmd)
        # logger.info("\t\tnoipf:   \t\t%s",self.noipf)
        # logger.info("\t\ttime:    \t\t%s",self.gtime)
        # logger.info("\t\tsendbuf:\t\t%s",self.sendbuf)
        # logger.info("\t\ttimezone:\t\t%s",self.tmzone)
        #logger.info("\tvalueoffset:\t\t%s",self.valueoffset)
        #logger.info("\toffset:\t\t%s",self.offset)
        # logger.info("\t\tinverterid:\t\t%s",self.inverterid)
        # logger.info("\t\tmode:    \t\t%s",self.mode)
        # logger.info("\t\tgrottip: \t\t%s",self.grottip)
        # logger.info("\t\tgrottport\t\t%s",self.grottport)
        #logger.info("\tSN\t\t%s",self.SN)
        #growatt
        #if (self.mode in ("server","serversa")) and (self.serverpassthrough) :
        # logger.info("_Growattserver:")
        # logger.info("\t\tgrowattip:\t\t%s",self.growattip)
        # logger.info("\t\tgrowattport:\t\t%s",self.growattport)
        #grottserver
        # if self.mode in ("server","serversa"):
        #     logger.info("_Grottserver:")
        #     #logger.info("\t\tconfserver:\t\t%s",self.confserver)
        #     logger.info("\t\tserverpassthrough:\t%s",self.serverpassthrough)
        #     logger.info("\t\tserverip:\t\t%s",self.serverip)
        #     logger.info("\t\tserverport:\t\t%s",self.serverport)
        #     logger.info("\t\thttpport:\t\t%s",self.httpport)
        #     logger.info("\t\tserverrespwait\t\t%s",self.serverrespwait)
        #     logger.info("\t\tserverirespwait:\t%s",self.serverirespwait)
        #     logger.info("\t\tserverlrespwait:\t%s",self.serverlrespwait)
        #Mqtt
        # if not self.nomqtt :
        #     logger.info("_MQTT:")
        #     logger.info("\t\tnomqtt:   \t\t%s",self.nomqtt)
        #     logger.info("\t\tmqttip:   \t\t%s",self.mqttip)
        #     logger.info("\t\tmqttport:\t\t%s",self.mqttport)
        #     logger.info("\t\tmqtttopic:\t\t%s",self.mqtttopic)
        #     logger.info("\t\tmqttmtopic:\t\t%s",self.mqttmtopic)
        #     logger.info("\t\tmqttmtopicname:\t\t%s",self.mqttmtopicname)
        #     logger.info("\t\tmqttinverterintopic:\t\t%s",self.mqttinverterintopic)
        #     logger.info("\t\tmqtttretain:\t\t%s",self.mqttretain)
        #     logger.info("\t\tmqtttauth:\t\t%s",self.mqttauth)
        #     logger.info("\t\tmqttuser:\t\t%s",self.mqttuser)
        #     logger.info("\t\tmqttpsw:\t\t%s","**secret**")                                                 #scrambleoutputiftested!
        #pvoutput
        if self.pvoutput :
            logger.info("_PVOutput:")
            logger.info("\t\tpvoutput:\t\t%s",self.pvoutput)
            logger.info("\t\tpvdisv1:\t\t%s",self.pvdisv1)
            logger.info("\t\tpvtemp:   \t\t%s",self.pvtemp)
            logger.info("\t\tpvurl:    \t\t%s",self.pvurl)
            logger.info("\t\tpvapikey:\t\t%s",self.pvapikey)
            logger.info("\t\tpvinverters:\t\t%s",self.pvinverters)
            if self.pvinverters==1:
                logger.info("\t\tpvsystemid:\t\t%s",self.pvsystemid[1])
            else:
                logger.info("\t\tpvsystemid:\t\t%s",self.pvsystemid)
                logger.info("\t\tpvinvertid:\t\t%s",self.pvinverterid)
        #Influx
        if self.influx :
            logger.info("_Influxdb:")
            logger.info("\t\tinflux: \t\t%s",self.influx)
            logger.info("\t\tinflux2:\t\t%s",self.influx2)
            logger.info("\t\tdatabase:\t\t%s",self.ifdbname)
            logger.info("\t\tip:      \t\t%s",self.ifip)
            logger.info("\t\tport:    \t\t%s",self.ifport)
            logger.info("\t\tuser:    \t\t%s",self.ifuser)
            logger.info("\t\tpassword:\t\t%s","**secret**")
            #logger.info("\tpassword:\t\t%s",self.ifpsw)
            logger.info("\t\torganization:\t\t%s",self.iforg)
            logger.info("\t\tbucket:  \t\t%s",self.ifbucket)
            logger.info("\t\ttoken:   \t\t%s","**secret**")
            #logger.info("\ttoken:\t\t%s",self.iftoken)
        #extension
        # if self.extension :
        #     logger.info("_Extension:")
        #     logger.info("\t\textension:\t\t%s",self.extension)
        #     logger.info("\t\textname:\t\t%s",self.extname)
        #     logger.info("\t\textvar:  \t\t%s",self.extvar)
        #     #add empty row
        #     logger.info("")

    def parserinit(self):
        """Process commandline parameters"""
        parser = argparse.ArgumentParser(prog='grott')
        parser.add_argument('-v','--verbose',help="set verbose",action='store_true')
        parser.add_argument('--version', action='version', version=self.verrel)
        parser.add_argument('-l','--log',help="set log level",metavar="[loglevel]")
        parser.add_argument('-c',help="set config file if not specified config file is grott.ini",metavar="[config file]")
        parser.add_argument('-o',help="set output file, if not specified output is stdout",metavar="[output file]")
        #get args
        args, unknown = parser.parse_known_args()
        #process args
        if (args.c != None) : self.cfgfile=args.c
        #if (args.o != None) : sys.stdout = open(args.o, 'wb',0) changed to support unbuffered output in windows !!!
        if (args.o != None) : sys.stdout = io.TextIOWrapper(open(args.o, 'wb', 0), write_through=True)
        if (args.log != None) :
            self.loglevel=args.log.upper()
            if self.loglevel.upper() in ("DEBUG","DEBUGV") : self.verbose = True
        elif (args.verbose != None) :
            self.verbose = args.verbose
            print(args.verbose)
            if self.verbose : self.loglevel = "DEBUG"
        logger.setLevel(self.loglevel.upper())
        #show args
        logger.info("Grott Command line parameters processed:")
        logger.info("\t- verbose:   \t\t%s", self.verbose)
        logger.info("\t- loglevel:   \t\t%s", self.loglevel)
        logger.info("\t- config file:\t\t%s", self.cfgfile)
        logger.info("\t- output file:\t\t%s", sys.stdout)

    def confpost(self):
        """Post processing after all parameters settings are read"""
        #set default grottip address
        if self.grottip == "default" :
            self.changeparm("grottip",'0.0.0.0')
        #set the grott ip/poort as the grottserver IP/poort while the server will be the entry point.
        #httplisterner will also use the same ip address.
        if self.mode == "server":
            self.changeparm("serverip",self.grottip)
            self.changeparm("serverport",self.grottport)
        # correct settings if changed by parameter processing
        # might better be moved to the processing sections?

        logger.info("Correct parameter settings if needed")

        #290 if hasattr(self, "amode"):
        #290     self.mode = self.amode
        #290 if hasattr(self, "ablockcmd") and self.ablockcmd == True:
        #290     self.blockcmd = self.ablockcmd
        #290 if hasattr(self, "anoipf") and self.anoipf == True:
        #290     self.noipf = self.anoipf
        #290 if hasattr(self, "ainverterid"):
        #290     self.inverterid = self.ainverterid
        #290 if hasattr(self, "anomqtt") and self.anomqtt:
        #290     self.nomqtt = self.anomqtt
        #290 if hasattr(self, "apvoutput") and self.apvoutput:
        #290     self.pvoutput = self.apvoutput
        #Correct Bool if changed to string during parsing process
        # if self.verbose == True or self.verbose == "True" : self.verbose = True
        # else : self.verbose = False
        self.verbose = str2bool(self.verbose)
        #290 self.trace = str2bool(self.trace)
        #290 self.decrypt = str2bool(self.decrypt)
        #290 self.compat = str2bool(self.compat)
        self.includeall = str2bool(self.includeall)
        self.blockcmd = str2bool(self.blockcmd)
        self.noipf = str2bool(self.noipf)
        self.sendbuf = str2bool(self.sendbuf)
        #
        self.serverpassthrough = str2bool(self.serverpassthrough)
        #
        self.nomqtt = str2bool(self.nomqtt)
        self.mqttmtopic = str2bool(self.mqttmtopic)
        self.mqttauth = str2bool(self.mqttauth)
        self.mqttretain = str2bool(self.mqttretain)
        #
        self.pvoutput = str2bool(self.pvoutput)
        self.pvdisv1 = str2bool(self.pvdisv1)
        self.pvtemp = str2bool(self.pvtemp)
        #
        self.influx = str2bool(self.influx)
        self.influx2 = str2bool(self.influx2)
        self.extension = str2bool(self.extension)
        #Prepare invert settings
        self.SN = "".join(['{:02x}'.format(ord(x)) for x in self.inverterid])
        #3.0.0 self.offset = 6
        #set offset for older inverter types or after record change by Growatt
        #3.0.0 if self.compat: self.offset = int(self.valueoffset)
        #grott in standalone server mode no additional processing
        if self.mode == "serversa" :
            self.nomqtt = True
            self.pvoutput = False
            self.influxdb = False
            self.influxdb2 = False
    def procconf(self):
        logger.info("Grott process configuration file")
        config = configparser.ConfigParser()
        config.read(self.cfgfile)
        if config.has_option("Generic","minrecl"): self.minrecl = config.getint("Generic","minrecl")
        if config.has_option("Generic","verbose"): self.verbose = config.getboolean("Generic","verbose")
        if config.has_option("Generic","loglevel"): self.loglevel = config.get("Generic","loglevel")
        if config.has_option("Generic","decrypt"): self.decrypt = config.getboolean("Generic","decrypt")
        if config.has_option("Generic","compat"): self.compat = config.getboolean("Generic","compat")
        if config.has_option("Generic","includeall"): self.includeall = config.getboolean("Generic","includeall")
        if config.has_option("Generic","invtype"): self.invtype = config.get("Generic","invtype")
        if config.has_option("Generic","invtypemap"): self.invtypemap = eval(config.get("Generic","invtypemap"))
        if config.has_option("Generic","inverterid"): self.inverterid = config.get("Generic","inverterid")
        if config.has_option("Generic","blockcmd"): self.blockcmd = config.get("Generic","blockcmd")
        if config.has_option("Generic","noipf"): self.noipf = config.get("Generic","noipf")
        if config.has_option("Generic","time"): self.gtime = config.get("Generic","time")
        if config.has_option("Generic","sendbuf"): self.sendbuf = config.get("Generic","sendbuf")
        if config.has_option("Generic","timezone"): self.tmzone = config.get("Generic","timezone")
        if config.has_option("Generic","mode"): self.mode = config.get("Generic","mode")
        if config.has_option("Generic","ip"): self.grottip = config.get("Generic","ip")
        if config.has_option("Generic","port"): self.grottport = config.getint("Generic","port")
        if config.has_option("Generic","valueoffset"): self.valueoffset = config.get("Generic","valueoffset")
        #
        if config.has_option("Growatt","ip"): self.growattip = config.get("Growatt","ip")
        if config.has_option("Growatt","port"): self.growattport = config.getint("Growatt","port")
        #Server
        if config.has_option("Server","serverpassthrough"): self.serverpassthrough = config.getboolean("Server","serverpassthrough")
        if config.has_option("Server","fullproxy"): self.fullproxy = config.get("Server","fullproxy")
        if config.has_option("Server","serverip"): self.serverip = config.get("Server","serverip")
        if config.has_option("Server","serverport"): self.serverport = config.getint("Server","serverport")
        if config.has_option("Server","httpport"): self.httpport = config.getint("Server","httpport")
        if config.has_option("Server","apirespwait"): self.apirespwait = config.getfloat("Server","apirespwait")
        if config.has_option("Server","inverterrespwait"): self.inverterrespwait = config.getint("Server","inverterrespwait")
        if config.has_option("Server","dataloggerrespwait"): self.dataloggerrespwait = config.getint("Server","dataloggerrespwait")
        if config.has_option("Server","ConnectionTimeout"): self.ConnectionTimeout = config.getint("Server","ConnectionTimeout")
        #mqtt
        if config.has_option("MQTT","nomqtt"): self.nomqtt = config.get("MQTT","nomqtt")
        if config.has_option("MQTT","ip"): self.mqttip = config.get("MQTT","ip")
        if config.has_option("MQTT","port"): self.mqttport = config.getint("MQTT","port")
        if config.has_option("MQTT","topic"): self.mqtttopic = config.get("MQTT","topic")
        if config.has_option("MQTT","mtopic"): self.mqttmtopic = config.get("MQTT","mtopic")
        if config.has_option("MQTT","mtopicname"): self.mqttmtopicname = config.get("MQTT","mtopicname")
        if config.has_option("MQTT","inverterintopic"): self.mqttinverterintopic = config.getboolean("MQTT","inverterintopic")
        if config.has_option("MQTT","retain"): self.mqttretain = config.getboolean("MQTT","retain")
        if config.has_option("MQTT","auth"): self.mqttauth = config.getboolean("MQTT","auth")
        if config.has_option("MQTT","user"): self.mqttuser = config.get("MQTT","user")
        if config.has_option("MQTT","password"): self.mqttpsw = config.get("MQTT","password")
        if config.has_option("PVOutput","pvoutput"): self.pvoutput = config.get("PVOutput","pvoutput")
        if config.has_option("PVOutput","pvtemp"): self.pvtemp = config.get("PVOutput","pvtemp")
        if config.has_option("PVOutput","pvdisv1"): self.pvdisv1 = config.get("PVOutput","pvdisv1")
        if config.has_option("PVOutput","pvinverters"): self.pvinverters = config.getint("PVOutput","pvinverters")
        if config.has_option("PVOutput","apikey"): self.pvapikey = config.get("PVOutput","apikey")
        if config.has_option("PVOutput", "pvuplimit"): self.pvuplimit = config.getint("PVOutput", "pvuplimit")
        # if more inverter are installed at the same interface (shinelink) get systemids
        #if self.pvinverters > 1 :
        for x in range(self.pvinverters+1) :
            if config.has_option("PVOutput","systemid"+str(x)): self.pvsystemid[x] = config.get("PVOutput","systemid" + str(x))
            if config.has_option("PVOutput","inverterid"+str(x)): self.pvinverterid[x] = config.get("PVOutput","inverterid" + str(x))
        if self.pvinverters == 1 :
            if config.has_option("PVOutput","systemid"): self.pvsystemid[1] = config.get("PVOutput","systemid")
        #INFLUX
        if config.has_option("influx","influx"): self.influx = config.get("influx","influx")
        if config.has_option("influx","influx2"): self.influx2 = config.get("influx","influx2")
        if config.has_option("influx","dbname"): self.ifdbname = config.get("influx","dbname")
        if config.has_option("influx","ip"): self.ifip = config.get("influx","ip")
        if config.has_option("influx","port"): self.ifport = int(config.get("influx","port"))
        if config.has_option("influx","user"): self.ifuser = config.get("influx","user")
        if config.has_option("influx","password"): self.ifpsw = config.get("influx","password")
        if config.has_option("influx","org"): self.iforg = config.get("influx","org")
        if config.has_option("influx","bucket"): self.ifbucket = config.get("influx","bucket")
        if config.has_option("influx","token"): self.iftoken = config.get("influx","token")
        #extensionINFLUX
        if config.has_option("extension","extension"): self.extension = config.get("extension","extension")
        if config.has_option("extension","extname"): self.extname = config.get("extension","extname")
        if config.has_option("extension","extvar"): self.extvar = eval(config.get("extension","extvar"))

    def getenv(self, envvar):
        envval = os.getenv(envvar)

        if self.verbose: print(f"\n\tPulled '{envvar}={envval}' from the environment")
        return envval

    def procenv(self):
        logger.info("Grott process environmental variables")
        if os.getenv('gmode') in ("sniff", "proxy", "server", "serversa") :  self.mode = self.getenv('gmode')
        if os.getenv('gverbose') != None :  self.verbose = self.getenv('gverbose')
        if os.getenv('gminrecl') != None :
            if 0 <= int(os.getenv('gminrecl')) <= 255  :     self.minrecl = self.getenv('gminrecl')
        if os.getenv('gdecrypt') != None : self.decrypt = self.getenv('gdecrypt')
        if os.getenv('gcompat') != None :  self.compat = self.getenv('gcompat')
        if os.getenv('gincludeall') != None :  self.includeall = self.getenv('gincludeall')
        if os.getenv('ginvtype') != None :  self.invtype = self.getenv('ginvtype')
        if os.getenv('ginvtypemap') != None :  self.invtypemap = eval(self.getenv('ginvtypemap'))
        if os.getenv('gblockcmd') != None : self.blockcmd = self.getenv('gblockcmd')
        if os.getenv('gnoipf') != None : self.noipf = self.getenv('gnoipf')
        if os.getenv('gtime') in ("auto", "server") : self.gtime = self.getenv('gtime')
        if os.getenv('gtimezone') != None : self.tmzone = self.getenv('gtimezone')
        if os.getenv('gsendbuf') != None : self.sendbuf = self.getenv('gsendbuf')
        if os.getenv('ginverterid') != None :  self.inverterid = self.getenv('ginverterid')
        if os.getenv('ggrottip') != None :
            try:
                ipaddress.ip_address(os.getenv('ggrottip'))
                self.grottip = self.getenv('ggrottip')
            except:
                if self.verbose : print("\nGrott IP address env invalid")
        if os.getenv('ggrottport') != None :
            if 0 <= int(os.getenv('ggrottport')) <= 65535  :  self.grottport = self.getenv('ggrottport')
        if os.getenv('gvalueoffset') != None :
            if 0 <= int(os.getenv('gvalueoffset')) <= 255  :  self.valueoffset = self.getenv('gvalueoffset')
        if os.getenv('ggrowattip') != None :
            try:
                ipaddress.ip_address(os.getenv('ggrowattip'))
                self.growattip = self.getenv('ggrowattip')
            except:
                if self.verbose : print("\nGrott Growatt server IP address env invalid")
        if os.getenv('ggrowattport') != None :
            if 0 <= int(os.getenv('ggrowattport')) <= 65535  :  self.growattport = int(self.getenv('ggrowattport'))
            else :
                if self.verbose : print("\nGrott Growatt server Port address env invalid")
        #handle Serever environmentals
        if os.getenv('ConnectionTimeout') != None :  self.nomqtt = self.getenv('ConnectionTimeout')
        #handle mqtt environmentals
        if os.getenv('gnomqtt') != None :  self.nomqtt = self.getenv('gnomqtt')
        if os.getenv('gmqttip') != None :
            try:
                ipaddress.ip_address(os.getenv('gmqttip'))
                self.mqttip = self.getenv('gmqttip')
            except:
                if self.verbose : print("\nGrott MQTT server IP address env invalid")
        if os.getenv('gmqttport') != None :
            if 0 <= int(os.getenv('gmqttport')) <= 65535  :  self.mqttport = int(self.getenv('gmqttport'))
            else :
                if self.verbose : print("\nGrott MQTT server Port address env invalid")

        if os.getenv('gmqtttopic') != None :  self.mqtttopic = self.getenv('gmqtttopic')
        if os.getenv('gmqttinverterintopic') != None : self.mqttinverterintopic = self.getenv('gmqttinverterintopic')
        if os.getenv('gmqttmtopic') != None :  self.mqttmtopic = self.getenv('gmqttmtopic')
        if os.getenv('gmqttmtopicname') != None :  self.mqttmtopicname = self.getenv('gmqttmtopicname')
        if os.getenv('gmqttretain') != None :  self.mqttretain = self.getenv('gmqttretain')
        if os.getenv('gmqttauth') != None :  self.mqttauth = self.getenv('gmqttauth')
        if os.getenv('gmqttuser') != None :  self.mqttuser = self.getenv('gmqttuser')
        if os.getenv('gmqttpassword') != None : self.mqttpsw = self.getenv('gmqttpassword')
        #Handle PVOutput variables
        if os.getenv('gpvoutput') != None :  self.pvoutput = self.getenv('gpvoutput')
        if os.getenv('gpvtemp') != None :  self.pvtemp = self.getenv('gpvtemp')
        if os.getenv('gpvdisv1') != None :  self.pvdisv1 = self.getenv('gpvdisv1')
        if os.getenv('gpvapikey') != None :  self.pvapikey = self.getenv('gpvapikey')
        if os.getenv('gpvinverters') != None :  self.pvinverters = int(self.getenv('gpvinverters'))
        for x in range(self.pvinverters+1) :
                if os.getenv('gpvsystemid'+str(x)) != None :  self.pvsystemid[x] = self.getenv('gpvsystemid'+ str(x))
                if os.getenv('gpvinverterid'+str(x)) != None :  self.pvinverterid[x] = self.getenv('gpvinverterid'+ str(x))
        if self.pvinverters == 1 :
            if os.getenv('gpvsystemid') != None :  self.pvsystemid[1] = self.getenv('gpvsystemid')
        if os.getenv('pvuplimit') != None :  self.pvuplimit = int(self.getenv('pvuplimit'))
        #Handle Influx
        if os.getenv('ginflux') != None :  self.influx = self.getenv('ginflux')
        if os.getenv('ginflux2') != None :  self.influx2 = self.getenv('ginflux2')
        if os.getenv('gifdbname') != None :  self.ifdbname = self.getenv('gifdbname')
        if os.getenv('gifip') != None :
            try:
                ipaddress.ip_address(os.getenv('gifip'))
                self.ifip = self.getenv('gifip')
            except:
                if self.verbose : print("\nGrott InfluxDB server IP address env invalid")
        if os.getenv('gifport') != None :
            if 0 <= int(os.getenv('gifport')) <= 65535  :  self.ifport = int(self.getenv('gifport'))
            else :
                if self.verbose : print("\nGrott InfluxDB server Port address env invalid")
        if os.getenv('gifuser') != None :  self.ifuser = self.getenv('gifuser')
        if os.getenv('gifpassword') != None :  self.ifpsw = self.getenv('gifpassword')
        if os.getenv('giforg') != None :  self.iforg = self.getenv('giforg')
        if os.getenv('gifbucket') != None :  self.ifbucket = self.getenv('gifbucket')
        if os.getenv('giftoken') != None :  self.iftoken = self.getenv('giftoken')
        #Handle Extension
        if os.getenv('gextension') != None :  self.extension = self.getenv('gextension')
        if os.getenv('gextname') != None :  self.extname = self.getenv('gextname')
        if os.getenv('gextvar') != None :  self.extvar = eval(self.getenv('gextvar'))

    def procinflux(self):
        #Influx db initialisation
        if self.ifip == "localhost" : self.ifip = '0.0.0.0'
        if self.influx2 == False:
            logger.info("Grott InfluxDB V1 initiating started")
            try:
                from influxdb import InfluxDBClient
            except:
                return(4,"Grott Influxdb Library not installed in Python")

            self.influxclient = InfluxDBClient(host=self.ifip, port=self.ifport, timeout=3, username=self.ifuser, password=self.ifpsw)

            try:
                databases = [db['name'] for db in self.influxclient.get_list_database()]
            except Exception as e:
                return(4,"Grott can not connect to InfluxDB")

            if self.ifdbname not in databases:
                logger.info("Grott %s database not yet defined, will be created",self.ifdbname)
                try:
                    self.influxclient.create_database(self.ifdbname)
                except Exception as e:
                    return(4,"influxDB error: {0} - {1}".format(e.code,e.content))

            self.influxclient.switch_database(self.ifdbname)
            return(0,"InfluxDB V1 initiation completed for: "+self.ifdbname)

        else:
            #influxDB V2 initiatlisation
            logger.info("Grott InfluxDB V2 initiating started")
            try:
                from influxdb_client import InfluxDBClient
                from influxdb_client.client.write_api import SYNCHRONOUS
            except:
                return(4,"Grott Influxdb-client Library not installed in Python")

            #self.influxclient = InfluxDBClient(url='192.168.0.211:8086',org=self.iforg, token=self.iftoken)
            self.influxclient = InfluxDBClient(url="{}:{}".format(self.ifip, self.ifport),org=self.iforg, token=self.iftoken)
            self.ifbucket_api = self.influxclient.buckets_api()
            self.iforganization_api = self.influxclient.organizations_api()
            self.ifwrite_api = self.influxclient.write_api(write_options=SYNCHRONOUS)

            try:
                buckets = self.ifbucket_api.find_bucket_by_name(self.ifbucket)
                organizations = self.iforganization_api.find_organizations()
                #print(organizations)
                if buckets == None:
                    #print("\t - " + "influxDB bucket ", self.ifbucket, "not defined")
                    #self.influx = False
                    #raise SystemExit("Grott Influxdb initialisation error")
                    return(4,"influxDB bucket {0}, not defined".format(self.ifbucket))

                orgfound = False
                for org in organizations:
                    if org.name == self.iforg:
                        orgfound = True
                        break
                if not orgfound:
                    return(4,"influxDB organization : {0} not defined or no authorised".format(self.iforg))

            except Exception as e:
                #if self.verbose :  print("\t - " + "Grott error: can not contact InfluxDB",e.message)
                #self.influx = False                       # no influx processing any more till restart (and errors repared)
                #raise SystemExit("Grott Influxdb initialisation error")
                return(4,"Grott error: can not contact InfluxDB: {0} - {1}".format(e.status,e.message))

            return(0,"InfluxDB V2 initiation completed for - {0}: {1}".format(self.iforg,self.ifdbname))



    def set_recwl(self):
        #define record that will not be blocked or inspected if blockcmd is specified
        self.recwl = {"0103",                                    #announce record
                         "0104",                                    #data record
                         "0116",                                    #ping
                         "0105",                                    #identify/display inverter config
                         "0119",                                    #identify/display datalogger config
                         "0120",                                    #Smart Monitor Record
                         "0150",                                    #Archived record
                         "5003",                                    #announce record
                         "5004",                                    #data record
                         "5016",                                    #ping
                         "5005",                                    #identify/display inverter config
                         "5019",                                    #identify/display datalogger config
                         "501b",                                    #SDM630 with Raillog
                         "5050",                                    #Archived record
                         "5103",                                    #announce record
                         "5104",                                    #data record
                         "5116",                                    #ping
                         "5105",                                    #identify/display inverter config
                         "5119",                                    #identify/display datalogger config
                         "5129",                                    #announce record
                         "5150",                                    #Archived record
                         "5103",                                    #announce record
                         "5104",                                    #data record
                         "5216",                                    #ping
                         "5105",                                    #identify/display inverter config
                         "5219",                                    #identify/display datalogger config
                         "5229",                                    #announce record
                         "5250"                                     #Archived record

        }

        try:
            with open('recwl.txt') as f:
                self.recwl = f.read().splitlines()
            logger.info("Grott read external record whitelist: 'recwl.txt'")
        except:
            logger.info("Grott external record whitelist 'recwl.txt' not found")
        logger.debug("\t- Grott records whitelisted : \n {0}".format(format_multi_line("\t", str(self.recwl))))
    
    def loadDictJson(self, directoryName, targetDict):
        logger.info(f"Grott process external json files {directoryName}")
        for (dirpath, dirnames, filenames) in walk(directoryName):
            for filename in filenames:   
                file_path = os.path.join(dirpath, filename)
                if filename.endswith('.json'):
                    logger.info(f"Processing {file_path}")
                    with open(file_path) as json_file:
                        try:
                            dicttemp = json.load(json_file)
                            targetDict.update(dicttemp)
                        except json.JSONDecodeError as e:
                            # Handle JSON syntax errors
                            logger.error(f"JSON syntax error in file '{file_path}': {e}")
                        except FileNotFoundError:
                            # Handle file not found errors
                            logger.error(f"File not found: '{file_path}'")
                        except Exception as e:
                            # Handle other unexpected errors
                            logger.error(f"An unexpected error occurred: {e}")
        
    def saveDictJson(self, directoryName, sourceDict):
        logger.info(f"Grott save record configs to  json files to {directoryName}")
        if not os.path.exists(directoryName):
            os.makedirs(directoryName)
        for key in sourceDict :
            file_path = os.path.join(directoryName, f"{key}.json")
            logger.info(f"Saving {file_path}")
            with open(file_path, 'w') as json_file:
                try:
                    json.dump({key: sourceDict[key]}, json_file, indent=4)
                except Exception as e:
                    # Handle other unexpected errors
                    logger.error(f"An unexpected error occurred while saving {file_path}: {e}")

    def saveRecordJson(self, recordEntry):
        logger.info(f"Grott save record config to  json file for {recordEntry}")
        directoryName = "recorddict"
        if not os.path.exists(directoryName):
            os.makedirs(directoryName)
        file_path = os.path.join(directoryName, f"{recordEntry}.json")
        logger.info(f"Saving {file_path}")
        with open(file_path, 'w') as json_file:
            try:
                json.dump({recordEntry: self.recorddict[recordEntry]}, json_file, indent=4)
            except Exception as e:
                # Handle other unexpected errors
                logger.error(f"An unexpected error occurred while saving {file_path}: {e}")
        
                                  
    def set_reclayouts(self):
        #define record layout to be used based on byte 4,6,7 of the header T+byte4+byte6+byte7
        self.recorddict = {}
        self.loadDictJson('recorddict', self.recorddict)
        self.alodict = {}
        # Layout definitions for automatic record detection
        self.loadDictJson('alodict', self.alodict)

        logger.info("Grott layout records loaded")

        for key in self.recorddict :
            #logger.info("\t{0}".format(key,format_multi_line("\t", str(self.recorddict[key]),120)))
            logger.info("\t{0}".format(key))
            logger.debugv("\n{0}\n".format(format_multi_line("\t", str(self.recorddict[key]),120)))

        for key in self.alodict :
            logger.info("\t{0}".format(key))
            logger.debugv("\n{0}\n".format(format_multi_line("\t", str(self.alodict[key]),120)))

if __name__ == "__main__":
    """main module: be aware this is only executed for testing this module"""
    from grottserver import addLoggingLevel
    addLoggingLevel("DEBUGV", 5)
    logging.basicConfig(level=logging.DEBUGV)
 #   logger.setLevel("DEBUGV")
    conf = Conf("testHenry")
 
    import jsons # pip install jsons , this is used to convert class to json
    json_data = jsons.dumps(conf)
    data = json.loads(json_data)

    # Pretty-print the JSON with indentation
    pretty_json = json.dumps(data, indent=4)
#   print(pretty_json)
