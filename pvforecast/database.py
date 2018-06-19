# -*- coding: utf-8 -*-
"""
    theoptimization.database
    ~~~~~
    
    
"""
import logging
logger = logging.getLogger(__name__)

import os
import io
import json
import pytz as tz
import datetime as dt
import pandas as pd
import numpy as np

from abc import ABC, abstractmethod
from collections import OrderedDict
from configparser import ConfigParser
from emonpy import Emoncms, EmoncmsData


class DatabaseList(OrderedDict):

    def __init__(self, configs, **kwargs):
        super(DatabaseList, self).__init__(**kwargs)
        
        # Read the systems database settings
        settingsfile = os.path.join(configs.get('General', 'configdir'), 'database.cfg')
        settings = ConfigParser()
        settings.read(settingsfile)
        
        timezone = settings.get('General', 'timezone')
        
        for database in settings.get('General', 'enabled').split(','):
            key = database.lower()
            if key == 'emoncms':
                self[key] = EmoncmsDatabase(configs, timezone=timezone)
                
            elif key == 'csv':
                self[key] = CsvDatabase(configs, timezone=timezone)


    def post(self, system, data, **kwargs):
        for database in reversed(self.values()):
            database.post(system, data, datatype='systems', **kwargs)


    def get(self, system, start, end, interval, **kwargs):
        database = next(iter(self.values()))
        database.get(system, start, end=end, interval=interval, datatype='systems', **kwargs)


class Database(ABC):

    def __init__(self, timezone='UTC'):
        self.timezone = tz.timezone(timezone)


    @abstractmethod
    def get(self, system, time, **kwargs):
        """ 
        Retrieve data for a specified time interval of a set of data feeds
        
        :param system: 
            the system for which the values will be looked up for.
        :type keys: 
            :class:`pvforecast.system.System`
        
        :param time: 
            the time for which the values will be looked up for.
            For many applications, passing datetime.datetime.now() will suffice.
        :type time: 
            :class:`pandas.tslib.Timestamp` or datetime
        
        :returns: 
            the retrieved values, indexed in a specific time interval.
        :rtype: 
            :class:`pandas.DataFrame`
        """
        pass


    @abstractmethod
    def post(self, system, data, **kwargs):
        """ 
        Post a set of data values, to persistently store them on the server
        
        :param system: 
            the system for which the values will be looked up for.
        :type keys: 
            :class:`pvforecast.system.System`
        
        :param data: 
            the data set to be posted
        :type data: 
            :class:`pandas.DataFrame`
        """
        pass


class EmoncmsDatabase(Database):

    def __init__(self, configs, timezone='UTC'):
        super().__init__(timezone=timezone)
        
        settingsfile = os.path.join(configs.get('General', 'configdir'), 'database.cfg')
        settings = ConfigParser()
        settings.read(settingsfile)
        
        emoncmsfile = settings.get('Emoncms', 'configs')
        emoncms = ConfigParser()
        emoncms.read(emoncmsfile)
        
        self.node = settings.get('Emoncms','node')
        self.connection = Emoncms(emoncms.get('Emoncms','address'), emoncms.get('Emoncms','authentication'))


    def get(self, system, start, end, interval, **kwargs):
        pass


    def post(self, system, data, **kwargs):
        if hasattr(system, 'apikey'):
            bulk = EmoncmsData(timezone=self.timezone)
            for key in data.columns:
                if len(data.columns) > 1:
                    name = key.replace(' ', '_').lower()
                else:
                    name = system.name.lower()
                
                for time, value in data[key].items():
                    if value is not None and not np.isnan(value):
                        bulk.add(time, self.node, name, float(value))
            
            self.connection.post(bulk, apikey=system.apikey)


class CsvDatabase(Database):

    def __init__(self, configs, timezone='UTC'):
        super().__init__(timezone=timezone)
        
        settingsfile = os.path.join(configs.get('General', 'configdir'), 'database.cfg')
        settings = ConfigParser()
        settings.read(settingsfile)
        
        self.datadir = configs.get('General', 'datadir')
        self.decimal = settings.get('CSV', 'decimal')
        self.separator = settings.get('CSV', 'separator')


    def exists(self, system, time, datatype='weather'):
        return os.path.exists(self._build_file(system, time, datatype))


    def get(self, system, start, end=None, interval=None, datatype='weather'):
        data = self._read_file(self._build_file(system, start, datatype))
        
        if interval is not None and interval > 900:
            offset = (start - start.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds() % interval
            data = data.resample(str(int(interval))+'s', base=offset).sum()
        
        if end is not None:
            if start > end:
                return data.truncate(before=start).head(1)
            
            return data.loc[start:end+dt.timedelta(seconds=interval)]
        
        return data


    def post(self, system, data, datatype='weather', **kwargs):
        if data is not None:
            path = self._build_dir(system, datatype)
            if not os.path.exists(path):
                os.makedirs(path)
            
            self._write_file(data, path, **kwargs)


    def _write_file(self, data, path, date):
        file = os.path.join(path, self._build_file_name(date))
        
        data.index.name = 'time'
        data.tz_convert(tz.utc).astype(float).to_csv(file, sep=self.separator, decimal=self.decimal, encoding='utf-8')


    def _read_file(self, path, index_column='time', unix=True):
        """
        Reads the content of a specified CSV file.
        
        :param path: 
            the full path to the CSV file.
        :type path:
            str or unicode
        
        :param index_column: 
            the name of the column, that will be used as index. The index will be assumed 
            to be a time format, that will be parsed and localized.
        :type index_column:
            str or unicode
        
        :param unix: 
            the flag, if the index column contains UNIX timestamps that need to be parsed accordingly.
        :type unix:
            boolean
        
        :param timezone: 
            the timezone, in which the data is logged and available in the data file.
            See http://en.wikipedia.org/wiki/List_of_tz_database_time_zones for a list of 
            valid time zones.
        :type timezone:
            str or unicode
        
        
        :returns: 
            the retrieved columns, indexed by their date
        :rtype: 
            :class:`pandas.DataFrame`
        """
        csv = pd.read_csv(path, sep=',', decimal='.', 
                          index_col=index_column, parse_dates=[index_column])
        
        if not csv.empty:
            if unix:
                csv.index = pd.to_datetime(csv.index, unit='ms')
                
            csv.index = csv.index.tz_localize(tz.utc)
        
        csv.index.name = 'time'
        
        return csv


    def _build_file(self, system, time, datatype):
        return os.path.join(self._build_dir(system, datatype), self._build_file_name(time))


    def _build_file_name(self, time):
        return time.astimezone(tz.utc).strftime('%Y%m%d_%H%M%S') + '.csv';


    def _build_dir(self, system, datatype):
        if datatype == 'weather':
            location_key = str(system.location.latitude) + '_' + str(system.location.longitude)
        else:
            location_key = system.location_key
        
        return os.path.join(self.datadir, datatype, location_key)


class JsonDatabase(object):

    def __init__(self, configs, key):
        self.datadir = configs.get('General', 'datadir')
        
        self.key = key
        
        self.sam_url = 'https://raw.githubusercontent.com/pvlib/pvlib-python/master/pvlib/data'
        self.sam_db = None


    def get(self, path):
        path = path.split('/')
        filename = os.path.join(self.datadir, self.key, path[0], path[1], path[2]+'.json')
        
        with open(filename, encoding='utf-8') as file:
            return json.load(file)


    def _write_json(self, path, data):
        filedir = os.path.join(self.datadir, self.key, path[0], path[1])
        if not os.path.exists(filedir):
            os.makedirs(filedir)
        
        filename = os.path.join(filedir, path[2]+'.json')
        with open(filename, 'w', encoding='UTF-8') as file:
            file.write(json.dumps(data, separators=(',', ':'))) #, indent=2)


    def _write_meta(self, data):
        count = 0
        for model, manufacturers in data.items():
            for manufacturer, modules in manufacturers.items():
                count += len(modules.keys())
                # Sort the meta data by module name before writing them as JSON
                modules = OrderedDict(sorted(modules.items(), key=lambda m: m[1]['Name']))
                
                meta_file = os.path.join(self.datadir, self.key, model, manufacturer+'.json')
                with open(meta_file, 'w', encoding='UTF-8') as file:
                    file.write(json.dumps(modules, separators=(',', ':'))) #, indent=2)
        
        return count


    def _load_cec(self):
        csv = os.path.join(self.datadir, self.key, 'cec.csv')
        return pd.read_csv(csv, skiprows=[1, 2], encoding = "ISO-8859-1", low_memory=False)


    def _load_cec_custom(self):
        csv = os.path.join(self.datadir, self.key, 'cec_custom.csv')
        return pd.read_csv(csv, skiprows=[1, 2], encoding = "ISO-8859-1", low_memory=False)


    def _load_cec_sam(self, download=False):
        try:
            from urllib2 import urlopen
        except ImportError:
            from urllib.request import urlopen
        
        if download:
            response = urlopen(self.sam_url + '/' + self.sam_db + '.csv')
            csv = io.StringIO(response.read().decode(errors='ignore'))
        else:
            csv = os.path.join(self.datadir, self.key, 'cec_sam.csv')
        
        return pd.read_csv(csv, skiprows=[1, 2], encoding = "ISO-8859-1", low_memory=False)


class ModuleDatabase(JsonDatabase):

    def __init__(self, configs):
        super(ModuleDatabase, self).__init__(configs, 'modules')
        
        self.sam_db = 'sam-library-cec-modules-2017-6-5'


    def build(self):
        db_cec = self._load_cec()
        db_sam = self._load_cec_sam()
        db_custom = self._load_cec_custom()
        
        db_meta = {}
        
        for _, module in pd.concat([db_cec, db_custom], sort=True).iterrows():
            module_sam = db_sam.loc[db_sam['Name'] == module['Manufacturer'] + ' ' + module['Model Number']]
            if len(module_sam) > 0:
                db_sam = db_sam.drop(module_sam.iloc[0].name)
                
                path, meta = self._parse_module_meta(module, 'singlediode')
                self._write_module_singlediode(path, module_sam.iloc[0].combine_first(module))
                
            elif not module.loc[['a_ref', 'I_L_ref', 'I_o_ref', 'R_sh_ref', 'R_s']].isnull().any():
                path, meta = self._parse_module_meta(module, 'singlediode')
                self._write_module_singlediode(path, module)
                
            else:
                path, meta = self._parse_module_meta(module, 'pvwatts')
                self._write_module_pvwatts(path, module)
            
            self._build_module_meta(db_meta, meta, *path)
            
            logger.debug("Successfully built Module: %s %s", meta['Manufacturer'], meta['Name'])
        
        db_count = self._write_meta(db_meta)
        
        file_remain = os.path.join(self.datadir, self.key, 'cec_sam_remain.csv')
        db_sam.to_csv(file_remain, encoding = "ISO-8859-1")
        
        logger.info("Complete module library built for %i entries", db_count)
        logger.debug("Unable to build %i SAM modules", len(db_sam))


    def _build_module_meta(self, database, meta, model, manufacturer, name):
        if model not in database:
            database[model] = {}
        
        if manufacturer not in database[model]:
            database[model][manufacturer] = {}
        
        database[model][manufacturer]['/'.join([model, manufacturer, name])] = meta


    def _parse_module_meta(self, module, model):
        meta = OrderedDict()
        meta['Name']         = module['Model Number']
        meta['Manufacturer'] = module['Manufacturer']
        meta['Description']  = module['Description']
        meta['BIPV']         = module['BIPV']
        
        manufacturer = meta['Manufacturer'].lower().replace(' ', '_').replace('/', '-').replace('&', 'n') \
                                           .replace(',', '').replace('.', '').replace('!', '') \
                                           .replace('(', '').replace(')', '')
        
        name = meta['Name'].lower().replace(' ', '_').replace('/', '-').replace('&', 'n') \
                                   .replace(',', '').replace('.', '').replace('!', '') \
                                   .replace('(', '').replace(')', '')
        
        path = [model, manufacturer, name]
        return path, meta


    def _write_module_singlediode(self, path, cec):
        module = OrderedDict()
        module['Date']          = cec['Date']
        module['Version']       = cec['Version']
        module['Technology']    = cec['Technology']
        module['BIPV']          = cec['BIPV']
        module['A_c']           = float(cec['A_c'])
        module['N_s']           = float(cec['N_s'])
        module['T_NOCT']        = float(cec['T_NOCT'])
        module['I_sc_ref']      = float(cec['I_sc_ref'])
        module['V_oc_ref']      = float(cec['V_oc_ref'])
        module['I_mp_ref']      = float(cec['I_mp_ref'])
        module['V_mp_ref']      = float(cec['V_mp_ref'])
        module['alpha_sc']      = float(cec['alpha_sc'])
        module['beta_oc']       = float(cec['beta_oc'])
        module['a_ref']         = float(cec['a_ref'])
        module['I_L_ref']       = float(cec['I_L_ref'])
        module['I_o_ref']       = float(cec['I_o_ref'])
        module['R_s']           = float(cec['R_s'])
        module['R_sh_ref']      = float(cec['R_sh_ref'])
        module['Adjust']        = float(cec['Adjust'])
        module['PTC']           = float(cec['PTC'])
        module['pdc0']          = float(cec['pdc0']) if not np.isnan(cec['pdc0']) else float(cec['Nameplate Pmax'])
        module['gamma_pdc']     = float(cec['gamma_pdc']) if not np.isnan(cec['gamma_pdc']) else float(cec['gamma_r'])/100.0
        module['gamma_r']       = float(cec['gamma_r'])
        
        self._write_json(path, module)


    def _write_module_pvwatts(self, path, cec):
        module = OrderedDict()
        module['Technology']    = cec['Technology']
        module['BIPV']          = cec['BIPV']
        module['A_c']           = float(cec['A_c'])
        module['N_s']           = float(cec['N_s'])
        module['T_NOCT']        = float(cec['Average NOCT'])
        module['PTC']           = float(cec['PTC'])
        module['pdc0']          = float(cec['Nameplate Pmax'])
        module['gamma_pdc']     = float(cec['?Pmax'])/100.0
        
        self._write_json(path, module)
