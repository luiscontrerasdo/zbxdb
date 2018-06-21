#!/usr/bin/env python
"""options: -c/--cfile configFile
                     configFile contains config for 1 database and
                                a reference to the checks directory"""
"""NOTE: a section whose name contains 'discover' is considered to be handled
           as a special case for LLD -> json arrays
   Drivers are loaded dynamically. If the driver is not installed this will
   raise an error and zbxdb will stop. Most problematic is the fact that all
   drivers seem to have their own results like errorcode errormsg. Sometimes
   only an errormsg is given, no code, sometimes the code is fully numeric,
   sometimes alphabetic. The template assumes that the errorcode is numeric 
   so zbxdb tries to extract a numeric part from the errorcode, if any.

   Do you need a new driver, help me with testing.
   (@) Ronald Rood - Ciber
"""
VERSION = "0.03"
import json
import collections
import datetime
import time
import sys
import os
import configparser
import resource
import gc
import subprocess
from optparse import OptionParser
from timeit import default_timer as timer
import platform

def printf(format, *args):
    """just a simple c-style printf function"""
    sys.stdout.write(format % args)
    sys.stdout.flush()

def output(config, key, values):
    """uniform way to generate the output"""
    timestamp = int(time.time())
    OUTF.write(config['hostname'] + " " + key + " " + str(timestamp) + " " + str(values)+ "\n")
    OUTF.flush()

def get_config (filename):
    config = {'db_url': "", 'db_type': "", 'db_driver': "", 'instance_type': "rdbms",
              'username': "scott", 'password': "tiger", 'role': "normal", 'omode': 0,
              'out_dir': "", 'out_file': "", 'hostname': "", 'checkfile_prefix': "",
              'site_checks': "", 'to_zabbic_method': "", 'to_zabbix_args': ""}
    CONFIG = configparser.RawConfigParser()
    if not os.path.exists(OPTIONS.configfile):
        raise ValueError("Configfile " + OPTIONS.configfile + " does not exist")

    INIF = open(filename, 'r')
    CONFIG.readfp(INIF)
    config['db_url'] = CONFIG.get(ME[0], "db_url")
    config['db_type'] = CONFIG.get(ME[0], "db_type")
    config['db_driver'] = CONFIG.get(ME[0], "db_driver")
    config['instance_type'] = CONFIG.get(ME[0], "instance_type")
    config['username'] = CONFIG.get(ME[0], "username")
    config['password'] = CONFIG.get(ME[0], "password")
    config['role'] = CONFIG.get(ME[0], "role")
    config['out_dir'] = os.path.expandvars(CONFIG.get(ME[0], "out_dir"))
    config['out_file'] = os.path.join(config['out_dir'], 
                                      str(os.path.splitext(os.path.basename(OPTIONS.configfile))[0]) + 
                                      ".zbx")
    config['hostname'] = CONFIG.get(ME[0], "hostname")
    config['checksfile_prefix'] = CONFIG.get(ME[0], "checks_dir")
    config['site_checks'] = CONFIG.get(ME[0], "site_checks")
    config['to_zabbix_method'] = CONFIG.get(ME[0], "to_zabbix_method")
    config['to_zabbix_args'] = os.path.expandvars(CONFIG.get(ME[0], "to_zabbix_args")) + \
                                                              " " + config['out_file']
    INIF.close()
    config['omode'] = 0
    if config['db_type'] == "oracle":
        if config['role'].upper() == "SYSASM":
            config['omode'] = db.SYSASM
        if config['role'].upper() == "SYSDBA":
            config['omode'] = db.SYSDBA

    if config['db_type'] == "oracle":
        config['CS'] = config['username'] + "/" + config['password'] + "@" + \
                       config['db_url'] + " as " + config['role'].upper()
    elif config['db_type'] == "postgres":
      config['CS'] = "postgresql://" + config['username'] + ":" + config['password'] + "@" + \
                       config['db_url']
    else:
        printf('%s DB_TYPE %s not -yet- implemented\n', 
               datetime.datetime.fromtimestamp(time.time()),
               config['db_type'])
        raise
    return config

def connection_info( dbtype ):
    conn_info = {'dbversion': "", 'sid': 0, 'itype': "rdbms", 
                 'serial': 0, 'dbrol': "", 'uname': "",
                 'iname': ""}
    CURS = conn.cursor()
    try:
        if dbtype == "oracle":
            CURS.execute("""select substr(i.version,0,instr(i.version,'.')-1),
              s.sid, s.serial#, p.value instance_type, i.instance_name
              , s.username
              from v$instance i, v$session s, v$parameter p 
              where s.sid = (select sid from v$mystat where rownum = 1)
              and p.name = 'instance_type'""" )
        elif dbtype == "postgres":
            CURS.execute("select substring(version from '[0-9]+') from version()")

        DATA = CURS.fetchone()
        conn_info['dbversion'] = DATA[0]

        if dbtype == "oracle":
            conn_info['sid'] = DATA[1]
            conn_info['serial'] = DATA[2]
            conn_info['itype'] = DATA[3]
            conn_info['iname'] = DATA[4]
            conn_info['uname'] = DATA[5]

    except db.DatabaseError as oerr:
        ERROR, = oerr.args
        if ERROR.code == 904:
            conn_info['dbversion'] = "pre9"
        else:
            conn_info['dbversion'] = "unk"
    if dbtype == "oracle" and ITYPE == "RDBMS":
        CURS.execute("""select database_role from v$database""" )
        DATA = CURS.fetchone()
        conn_info['dbrol'] = DATA[0]
    elif dbtype == "oracle":
        conn_info['dbrol'] = "asm"
    else:
        conn_info['dbrol'] = "primary"
    CURS.close()
    return conn_info

ME = os.path.splitext(os.path.basename(__file__))
PARSER = OptionParser()
PARSER.add_option("-c", "--cfile", dest="configfile", default=ME[0]+".cfg",
                  help="Configuration file", metavar="FILE")
(OPTIONS, ARGS) = PARSER.parse_args()

config = get_config(OPTIONS.configfile)

STARTTIME = int(time.time())
printf("%s start python-%s %s-%s pid=%s Connecting for hostname %s...\n", \
    datetime.datetime.fromtimestamp(STARTTIME), \
    platform.python_version(), ME[0], VERSION, os.getpid(), config['hostname']
      )
printf("%s %s found db_type=%s, driver %s; checking for driver\n",
    datetime.datetime.fromtimestamp(time.time()), ME[0], config['db_type'], config['db_driver'])
try:
  db= __import__(config['db_driver'])
except:
  printf("%s supported will be oracle(cx_Oracle), postgres(psycopg2), mysql(mysql.connector), mssql(pymssql/_mssql), db2(ibm_db_dbi)\n", ME[0])
  printf("%s tested are oracle(cx_Oracle), postgres(psycopg2)\n", ME[0])
  printf("Don't forget to install the drivers first ...\n")
  raise

printf("%s %s driver loaded\n", 
    datetime.datetime.fromtimestamp(time.time()), ME[0])
CHECKSCHANGED = [ 0 ]

CONNECTCOUNTER = 0
CONNECTERROR = 0
QUERYCOUNTER = 0
QUERYERROR = 0
if config['site_checks'] != "NONE":
    printf("%s site_checks: %s\n", \
        datetime.datetime.fromtimestamp(time.time()), config['site_checks'])
printf("%s to_zabbix_method: %s %s\n", \
    datetime.datetime.fromtimestamp(time.time()), config['to_zabbix_method'], config['to_zabbix_args'])
printf("%s out_file:%s\n", \
    datetime.datetime.fromtimestamp(time.time()), config['out_file'])
SLEEPC = 0
SLEEPER = 1
PERROR = 0
while True:
    try:
        config = get_config(OPTIONS.configfile)
        if os.path.exists(config['out_file']):
            OUTF = open(config['out_file'], "a")
        else:
            OUTF = open(config['out_file'], "w")

        if SLEEPC == 0:
            printf('%s connecting db_url:%s, type:%s, user:%s as %s\n',
                    datetime.datetime.fromtimestamp(time.time()), \
                    config['db_url'], config['db_type'], config['username'], config['role'])

        START = timer()
        with db.connect( config['CS'] ) as conn:
            CONNECTCOUNTER += 1
            output(config, ME[0]+"[connect,status]", 0)
            CURS = conn.cursor()
            connect_info = connection_info ( config['db_type'] )

            printf('%s connected db_url %s type %s db_role %s version %s\n%s user %s %s sid,serial %d,%d instance %s as %s\n',
                    datetime.datetime.fromtimestamp(time.time()), \
                    config['db_url'], connect_info['itype'], connect_info['dbrol'], \
                    connect_info['dbversion'], \
                    datetime.datetime.fromtimestamp(time.time()), \
                    config['username'], connect_info['uname'], connect_info['sid'], \
                    connect_info['serial'], \
                    connect_info['iname'], \
                    config['role'])
            if  connect_info['dbrol'] in ["PHYSICAL STANDBY", "MASTER"]:
                CHECKSFILE = os.patch.join(CHECKSFILE_PREFIX, DB_TYPE, "standby" + 
                                           "." + connect_info['dbversion'] +".cfg")
            else:
                CHECKSFILE = os.path.join(config['checksfile_prefix'], config['db_type']  , 
                                          connect_info['dbrol'] + "." + connect_info['dbversion']+".cfg")

            files= [ CHECKSFILE ]
            CHECKFILES = [ [ CHECKSFILE, 0]  ]
            if config['site_checks'] != "NONE":
                for addition in config['site_checks'].split(","):
                    addfile= os.path.join(config['checksfile_prefix'], config['db_type'],  
                                          addition + ".cfg")
                    CHECKFILES.extend( [ [ addfile, 0] ] )
                    files.extend( [ addfile ] )
            printf('%s using checks from %s\n',
                    datetime.datetime.fromtimestamp(time.time()), files)

            for CHECKSFILE in CHECKFILES:
              if not os.path.exists(CHECKSFILE[0]):
                  raise ValueError("Configfile " + CHECKSFILE[0]+ " does not exist")
            ## all checkfiles exist

            SLEEPC = 0
            SLEEPER = 1
            PERROR = 0
            CONMINS = 0
            while True:
                NOWRUN = int(time.time()) # keep this to compare for when to dump stats
                RUNTIMER = timer() # keep this to compare for when to dump stats
                if os.path.exists(config['out_file']):
                    OUTF = open(config['out_file'], "a")
                else:
                    OUTF = open(config['out_file'], "w")
                output(config, ME[0] + "[version]", VERSION)
                # loading checks from the various checkfiles:
                needToLoad = "no"
                for i in range(len(CHECKFILES)):
                    z=CHECKFILES[i]
                    CHECKSFILE = z[0]
                    CHECKSCHANGED = z[1]
                    if CHECKSCHANGED != os.stat(CHECKSFILE).st_mtime:
                        if CHECKSCHANGED == 0:
                            printf("%s checks loading %s\n", \
                                datetime.datetime.fromtimestamp(time.time()), CHECKSFILE)
                            needToLoad = "yes"
                        else:
                            printf("%s checks changed, reloading %s\n", \
                                datetime.datetime.fromtimestamp(time.time()), CHECKSFILE)
                            needToLoad = "yes"
                    
                if needToLoad == "yes":
                    OBJECTS_LIST = []
                    SECTIONS_LIST = []
                    for i in range(len(CHECKFILES)):
                        z=CHECKFILES[i]
                        CHECKSFILE = z[0]
                        CHECKSF = open(CHECKSFILE, 'r')
                        CHECKS = configparser.RawConfigParser()
                        CHECKS.readfp(CHECKSF)
                        CHECKSF.close()
                        z[1]= os.stat(CHECKSFILE).st_mtime
                        CHECKFILES[i] = z
                        for section in sorted(CHECKS.sections()):
                            printf("%s\t%s run every %d minutes\n", \
                                datetime.datetime.fromtimestamp(time.time()), section, \
                                int(CHECKS.get(section, "minutes")))
                            # dump own discovery items of the queries per section
                            E = collections.OrderedDict()
                            E = {"{#SECTION}": section}
                            SECTIONS_LIST.append(E)
                            x = dict(CHECKS.items(section))
                            for key, sql  in sorted(x.items()):
                                if sql and key != "minutes":
                                    d = collections.OrderedDict()
                                    d = {"{#SECTION}": section, "{#KEY}": key}
                                    OBJECTS_LIST.append(d)
                                    printf("%s\t\t%s: %s\n", \
                                        datetime.datetime.fromtimestamp(time.time()), \
                                        key, sql[0 : 60].replace('\n', ' ').replace('\r', ' '))
                    # checks are loaded now.
                    SECTIONS_JSON = '{\"data\":'+json.dumps(SECTIONS_LIST)+'}'
                    # printf ("DEBUG lld key: %s json: %s\n", ME[0]+".lld", ROWS_JSON)
                    output(config, ME[0]+".section.lld", SECTIONS_JSON)
                    ROWS_JSON = '{\"data\":'+json.dumps(OBJECTS_LIST)+'}'
                    # printf ("DEBUG lld key: %s json: %s\n", ME[0]+".lld", ROWS_JSON)
                    output(config, ME[0] + ".query.lld", ROWS_JSON)
                # checks discovery is also printed
                #
                # assume we are still connected. If not, exception will tell real story
                output(config, ME[0] + "[connect,status]", 0)
                # the connect status is only real if executed a query ....
                for section in sorted(CHECKS.sections()):
                    SectionTimer = timer() # keep this to compare for when to dump stats
                    if CONMINS % int(CHECKS.get(section, "minutes")) == 0:
                        ## time to run the checks again from this section
                        x = dict(CHECKS.items(section))
                        CURS = conn.cursor()
                        for key, sql  in sorted(x.items()):
                            if sql and key != "minutes":
                                # printf ("%s DEBUG Running %s.%s\n", \
                                    # datetime.datetime.fromtimestamp(time.time()), section, key)
                                try:
                                    QUERYCOUNTER += 1
                                    START = timer()
                                    CURS.execute(sql)
                                    startf = timer()
                                    # output for the query must include the complete key and value
                                    #
                                    rows = CURS.fetchall()
                                    if "discover" in section:
                                        OBJECTS_LIST = []
                                        for row in rows:
                                            d = collections.OrderedDict()
                                            for col in range(0, len(CURS.description)):
                                                d[CURS.description[col][0]] = row[col]
                                            OBJECTS_LIST.append(d)
                                        ROWS_JSON = '{\"data\":'+json.dumps(OBJECTS_LIST)+'}'
                                        # printf ("DEBUG lld key: %s json: %s\n", key, ROWS_JSON)
                                        output(config, key, ROWS_JSON)
                                        output(config, ME[0] + "[query," + section + "," + \
                                            key + ",status]", 0)
                                    else:
                                      if  len(rows) > 0 and len(rows[0]) == 2:
                                            for row in rows:
                                                # printf("DEBUG zabbix_host:%s zabbix_key:%s " + \
                                                    # "value:%s\n", HOSTNAME, row[0], row[1])
                                                output(config, row[0], row[1])
                                            output(config, ME[0] + "[query," + section + "," + \
                                                key + ",status]", 0)
                                      elif len(rows) == 0:
                                            output(config, ME[0] + "[query," + section + "," + \
                                                 key + ",status]", 0)
                                      else:
                                            printf('%s key=%s.%s zbxORA-%d: SQL format error: %s\n', \
                                                  datetime.datetime.fromtimestamp(time.time()), \
                                                  section, key, 2, "expect key,value pairs")
                                            output(config, ME[0] + "[query," + section + "," + \
                                                 key + ",status]", 2)
                                    fetchela = timer() - startf
                                    ELAPSED = timer() - START
                                    output(config, ME[0] + "[query," + section + "," + \
                                        key + ",ela]", ELAPSED)
                                    output(config, ME[0] + "[query," + section + "," + \
                                        key + ",fetch]", fetchela)
                                except db.DatabaseError as err:
                                    if config['db_driver'] == "psycopg2":
                                        errno= int(''.join(c for c in err.pgcode if c.isdigit()))
                                        ermsg= err.pgerror
                                    elif config['db_driver'] == "_mssql":
                                        errno= err.number
                                        ermsg= err.message
                                    else:
                                        errno= err.code
                                        ermsg= err.message
                                    conn.rollback()
                                        
                                    ELAPSED = timer() - START
                                    QUERYERROR += 1
                                    output(config, ME[0] + "[query," + section + "," + \
                                        key + ",status]", errno)
                                    printf('%s key=%s.%s ZBX-%d: Database execution error: %s\n', \
                                        datetime.datetime.fromtimestamp(time.time()), \
                                        section, key, errno, ermsg.strip().replace('\n',' ').replace('\r',' ') )
                                    if errno in(28, 1012, 3113, 3114, 3135):
                                        """ idea here is to close the connection
                                            when certain fatal exceptions occurred
                                            like - session killed
                                                 - instance down
                                                 - database down
                                                 - session disconnected
                                            so we can re-create a clean session
                                        """
                                        raise
                        # end of a section
                        output(config, ME[0] + "[query," + section + ",,ela]", \
                            timer() - SectionTimer)
                # dump metric for summed elapsed time of this run
                output(config, ME[0] + "[query,,,ela]", timer() - RUNTIMER)
                output(config, ME[0] + "[cpu,user]",  resource.getrusage(resource.RUSAGE_SELF).ru_utime)
                output(config, ME[0] + "[cpu,sys]",  resource.getrusage(resource.RUSAGE_SELF).ru_stime)
                output(config, ME[0] + "[mem,maxrss]",  resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
                # passed all sections
                if ((NOWRUN - STARTTIME) % 3600) == 0:
                    gc.collect()
                    # dump stats
                    printf("%s connect %d times, %d fail; started %d queries, " + \
                        "%d fail memrss:%d user:%f sys:%f\n", \
                        datetime.datetime.fromtimestamp(time.time()), \
                        CONNECTCOUNTER, CONNECTERROR, QUERYCOUNTER, QUERYERROR, \
                        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, \
                        resource.getrusage(resource.RUSAGE_SELF).ru_utime, \
                        resource.getrusage(resource.RUSAGE_SELF).ru_stime)
                # now pass data to zabbix, if possible
                if config['to_zabbix_method'] == "zabbix_sender":
                    STOUT = open(config['out_file'] + ".log", "w")
                    RESULT = subprocess.call(config['to_zabbix_args'].split(), \
                        shell=False, stdout=STOUT, stderr=STOUT)
                    if RESULT not in(0, 2):
                        printf("%s zabbix_sender failed: %d\n", \
                            datetime.datetime.fromtimestamp(time.time()), RESULT)
                    else:
                        OUTF.close()
                        # create a datafile / day
                        if datetime.datetime.now().strftime("%H:%M") < "00:10":
                            TOMORROW = datetime.datetime.now() + datetime.timedelta(days=1)
                            Z = open(config['out_file'] + "." + TOMORROW.strftime("%a"), 'w')
                            Z.close()

                        with open(config['OUT_FILE'] + "." + datetime.datetime.now().strftime("%a"), \
                            'a') as outfile:
                            with open(config['out_file'], "r") as infile:
                                outfile.write(infile.read())
                        OUTF = open(config['out_file'], "w")

                    STOUT.close()

                OUTF.close()
                # try to keep activities on the same starting second:
                SLEEPTIME = 60 - ((int(time.time()) - STARTTIME) % 60)
                # printf ("%s DEBUG Sleeping for %d seconds\n", \
                    # datetime.datetime.fromtimestamp(time.time()), SLEEPTIME)
                for i in range(SLEEPTIME):
                    time.sleep(1)
                CONMINS = CONMINS + 1 # not really mins since the checks could
                #                       have taken longer than 1 minute to complete
    except db.DatabaseError as err:
        if config['db_driver'] == "psycopg2":
            if err.pgcode is None:
                errno= 13
                ermsg= str(err)
            else:
                errno= int(''.join(c for c in err.pgcode if c.isdigit()))
                ermsg= err.pgerror
        elif config['db_driver'] == "_mssql":
            errno= err.number
            ermsg= err.message
        else:
            x, = err.args
            errno= x.code
            ermsg= x.message
        ELAPSED = timer() - START
        if errno not in (28, 1012, 3113, 3114, 3135):
            """
            NOT from a killed session or similar, so this was not a
            connect error but a returned session
            """
            CONNECTERROR += 1
        output(config, ME[0] + "[connect,status]", errno)
        if errno == 15000:
            """
            a special case for Oracle ASM instance when connecting
            using the NORMAL role. This should be SYSDBA since an
            ASM instance refuses NORMAL connections.
            """
            printf('%s: connection error: %s for %s@%s %s\n', \
                datetime.datetime.fromtimestamp(time.time()), \
                ermsg.strip().replace('\n', ' ').replace('\r', ' '), \
                USERNAME, DB_URL, ROLE)
            printf('%s: asm requires sysdba role instead of %s\n', \
            datetime.datetime.fromtimestamp(time.time()), ROLE )
            raise
        if PERROR != errno:
            SLEEPC = 0
            SLEEPER = 1
            PERROR = errno
        INIF.close()
        SLEEPC += 1
        if SLEEPC >= 10:
            if SLEEPER <= 301:
                # don't sleep longer than 5 mins after connect failures
                SLEEPER += 10
            SLEEPC = 0
        printf('%s: (%d.%d)connection error: %s for %s@%s\n', \
            datetime.datetime.fromtimestamp(time.time()), \
            SLEEPC, SLEEPER, ermsg.strip().replace('\n', ' ').replace('\r', ' '), \
            USERNAME, DB_URL)
        time.sleep(SLEEPER)
    except (KeyboardInterrupt, SystemExit):
        OUTF.close()
        raise

OUTF.close()
