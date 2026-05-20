import pandas as pd
import numpy as np
import pathlib
import matplotlib.pyplot as plt
import os
import json
from scipy.signal import butter, filtfilt
import utm
from enum import Enum, auto
try:
    from nodes.logger_ import logger_ as logger
except:
    class logger_:
        def __getattr__(self, name):
            return self
            
        def __call__(self, *args, **kwargs):
            return self

    logger = logger_()

SAVE_CSV = True

def load_json(path:str|pathlib.Path):
    if not os.path.exists(path):
        raise PermissionError(PostProcessCode.FILE_NOT_FOUND)
    with open(path, 'r') as f:
        return json.load(f)

class PostProcessCode(Enum):
    SUCCESS = auto()
    ERROR = auto()
    FILE_NOT_FOUND = auto()
    FILE_EMPTY = auto()
    MISSING_DATA_COLUMN = auto()
    MISSING_METADATA_FILE = auto()
    MISSING_TEST_CATALOG_FILE = auto()
    MISSING_TEST_CATALOG_FIELDS = auto()
    SCENARIO_NOT_SPECIFIED = auto()
    TEST_PASSED = auto()
    TEST_SOFT_PASSED = auto()
    TEST_FAILED = auto()
    TEST_INVALID = auto()
    UNKNOWN = auto()

class MissingSignalError(Exception):
    def __init__(self, column_name, message="Signal column is missing or all NaN"):
        self.message = message
        self.column_name = column_name
        self.msg = f"{message}: {column_name}"
        logger.logger.error(self.msg)
        super().__init__(self.msg)

class PostProcessingError(Exception):
    def __init__(self, code, message="Post processing failed with code"):
        self.code = code
        self.message = message
        self.msg = f"{message}: {code.name}"
        logger.logger.error(self.msg)
        super().__init__(self.msg)


class Catalog:
    root = '.configs/test_catalog.json'
    catalog = load_json(root)
    scenario_id = None
    current_scenario = {}
    prefix = ''
        
    @staticmethod
    def get_ignore_case(dict_, key_):
        for key, value in dict_.items():
            if key.lower() == key_.lower():
                return value
        return {}
        
    @classmethod
    def set_scenario(cls, scenario: int|str|None):
        if scenario is None:
            cls.scenario_id = None
            cls.current_scenario = {}

        if isinstance(scenario, int):
            scenario = str(scenario)

        if not scenario in cls.catalog['scenario_configs']:
            cls.scenario_id = None
            cls.current_scenario = {}
            raise PostProcessingError(PostProcessCode.MISSING_TEST_CATALOG_FIELDS)
        
        cls.scenario_id = scenario
        cls.current_scenario = cls.catalog['scenario_configs'][scenario]

    @classmethod
    def get(cls, field: str = ''):
        tmp = cls.current_scenario
        field = '.'.join([cls.prefix, field]) if cls.prefix else field
        for field_ in field.split('.'):
            if not field_:
                continue
            tmp = cls.get_ignore_case(tmp, field_)
        return tmp if tmp else None
    
    @classmethod
    def is_loaded(cls):
        return cls.catalog != {}
    
    @classmethod
    def is_scenario_set(cls):
        return cls.current_scenario != {}

    @classmethod
    def set_prefix(cls, prefix: str):
        cls.prefix = prefix

@pd.api.extensions.register_dataframe_accessor("hlm")
class CustomAccessor:
    def __init__(self, pandas_obj):
        self._obj = pandas_obj
    
    def reindex(self):
        """
        Reindex the dataframe to be sorted by time_nsec
        """
        if self._obj.index.name == 'time_nsec':
            self._obj.sort_index(inplace=True)
            return self._obj
        if 'time_nsec' not in self._obj.columns:
            return None
        self._obj.set_index('time_nsec', inplace=True)
        self._obj.sort_index(inplace=True)
        return self._obj

    def filter(self, col_name, value=pd.NA, keep=True):
        """
        Filter the dataframe by a column value
        Parameters
        ----------
        col_name: str
            Name of the column to filter by
        value: any
            Value to filter by
        keep: bool
            True to keep the rows where the column value is equal to the value, False to keep the rows where the column value is not equal to the value
        """
        if keep:
            return self._obj[self._obj[col_name] == value]
        else:
            return self._obj[self._obj[col_name] != value]
        
    def keep(self, cols: list, inplace=True):
        """
        Keep only the columns in the list
        """
        return self._obj.drop(columns=[col for col in self._obj.columns if col not in cols], inplace=inplace)
        
    def norm2(self, cols: list, col_name: str = 'norm2'):
        """
        Compute the norm2 of the columns in the list and store it in a new column
        Parameters
        ----------
        cols: list
            List of columns to compute the norm2 of
        col_name: str
            Name of the new column
        """
        self._obj[col_name] = self._obj[cols].pow(2).sum(axis=1).pow(0.5)
        return self._obj
    
    def from_latlon(self):
        """
        Convert latitude and longitude to easting and northing
        """        
        for col in ['Latitude[deg]', 'Longitude[deg]']:
            if col not in self._obj.columns:
                raise MissingSignalError(col)
                
        lat = self._obj['Latitude[deg]'].to_numpy()
        lon = self._obj['Longitude[deg]'].to_numpy()
        res = utm.from_latlon(lat, lon)
        self._obj['Easting[m]'] = res[0].reshape(-1)
        self._obj['Northing[m]'] = res[1].reshape(-1)
        dE = self._obj['Easting[m]'].diff()
        dN = self._obj['Northing[m]'].diff()
        dP = np.sqrt(dE**2 + dN**2)
        self._obj['Distance[m]'] = np.cumsum(dP)
        return self._obj
    
    def get_grade(self):
        """
        Compute the grade of the track
        """
        for col in ['Distance[m]', 'Altitude[m]']:
            if col not in self._obj.columns:
                raise MissingSignalError(col)
        height = self._obj['Altitude[m]'].to_numpy()
        dist = self._obj['Distance[m]'].to_numpy()
        grade = np.zeros_like(height)
        for idx in range(len(height)):
            if dist[idx] is np.nan:
                grade[idx] = np.nan
                continue
            mask = np.abs(dist - dist[idx]) <= 10
            if np.any(mask):
                grade[idx] = np.polyfit(dist[mask], height[mask], 1)[0]*100
            else:
                grade[idx] = np.nan
        self._obj['Grade[%]'] = grade            
        return self._obj
    
    def get_rolling_radii(self, window=100, min_points=5, radius_lim=1000):
        """
        DEPRECIATED
        Compute the rolling radii of the track
        Parameters
        ----------
        window: int
            Window size in meters
        min_points: int
            Minimum number of points in the window
        radius_lim: int
            Maximum radius in meters
        
        Returns
        ----------
        radius: np.ndarray
            Rolling radii of the track
        centers: np.ndarray
            Rolling centers of the track
        """
        for col in ['Northing[m]', 'Easting[m]']:
            if col not in self._obj.columns:
                raise MissingSignalError(col)
        x = self.obj_['Northing[m]'].to_numpy() - self.obj_['Northing[m]'].mean()
        y = self.obj_['Easting[m]'].to_numpy() - self.obj_['Easting[m]'].mean()
        dist = self.obj_['Distance[m]'].to_numpy()

        n = len(x)
        radius = np.full(n, np.nan)
        centers = [None] * n

        for i in range(n):
            if not np.isnan(radius[i]):
                continue
            mask = np.abs(dist - dist[i]) <= window
            if np.sum(mask) < min_points:
                continue

            A = np.column_stack([x[mask], y[mask], np.ones_like(x[mask])])
            B = x[mask]**2 + y[mask]**2

            c, *_ = np.linalg.lstsq(A, B, rcond=None)

            xc, yc = c[0]/2, c[1]/2
            R = np.sqrt(c[2] + xc**2 + yc**2)

            if R > radius_lim:
                continue

            radius[mask] = R
            centers[i] = (xc, yc)
        return radius, centers, x, y

    def get_curve_radius(self, mask: None | np.ndarray = None):
        """
        Compute the curve radius of the track
        Parameters
        ----------
        mask: np.ndarray
            Boolean mask

        Returns
        ----------
        radius: np.ndarray
            Curve radius of the track 
        Columns
        ----------
        Curve_Radius[m]
        """
        for col in ['Northing[m]', 'Easting[m]']:
            if col not in self._obj.columns:
                raise MissingSignalError(col)
            
        x = self._obj['Northing[m]'].to_numpy()
        y = self._obj['Easting[m]'].to_numpy()
        if mask is None and all([col in self._obj.columns for col in ['Range[m]', 'In_range']]):
            mask = self._obj['In_range'].to_numpy()
        A = np.column_stack([x[mask], y[mask], np.ones_like(x[mask])])
        B = x[mask]**2 + y[mask]**2
        c, *_ = np.linalg.lstsq(A, B, rcond=None)
        xc, yc = c[0]/2, c[1]/2
        R = np.sqrt(c[2] + xc**2 + yc**2)
        self._obj['Curve_Radius[m]'] = np.sqrt((x - xc)**2 + (y - yc)**2)
        return self._obj
    
    def fix_lux(self, gain = 6.8, cols=[]):
        """
        Fix the lux values
        Parameters
        ----------
        gain: float
            Gain of the lux sensor
        cols: list
            List of columns to fix
        """
        cols = cols if cols else self._obj.columns
        for col in cols:
            if col in self._obj.columns:
                self._obj[col] *= gain
        return self._obj
    
    def get_superelevation(self):
        """
        DEPRECIATED
        """
        if 'Roll[rad]' not in self._obj.columns:
            raise MissingSignalError('Roll[rad]')
        roll = self._obj['Roll[rad]'].to_numpy()
        S_ = np.tan(roll)
        self._obj['Superelevation[%]'] = S_*100
        return self._obj
    
    def butterworth_filter(self, cutoff, order, cols=[], inplace=True):
        """
        Butterworth filter
        Parameters
        ----------
        cutoff: float
            Cutoff frequency in Hz
        order: int
            Order of the filter
        cols: list
            List of columns to filter
        """
        target = self._obj if inplace else self._obj.copy()
        freq = np.nanmean(1/target.index.diff().to_numpy()*1e9)
        cols = cols if cols else target.columns
        b, a = butter(N=order, Wn=cutoff, fs=freq, btype='lowpass', analog=False)
        for col in cols:
            if col in target:
                y = target[col].dropna()
                h_filtered = filtfilt(b, a, y)
                y_serries = pd.Series(h_filtered, index=y.index)
                target[col] = y_serries
        return target
    
    def add(self, ref, interp=False):
        """
        Add two dataframes
        Parameters
        ----------
        ref: pd.DataFrame
            Reference dataframe
        interp: bool
            Interpolate the reference dataframe
        """
        if interp:
            combined_index = self._obj.index.union(ref.index)
            ref_tmp = ref.reindex(combined_index).interpolate(method='linear')
        else:
            ref_tmp = ref.copy()
        self._obj = pd.merge_asof(
            self._obj.sort_index(), 
            ref_tmp.sort_index(), 
            left_index=True, 
            right_index=True,
            direction='nearest',)
        return self._obj
    
    def set_zero(self, ref:dict = None):
        """
        Adjust zero for each column of the dataframe
        Parameters
        ----------
        ref: dict
            Dictionary of columns to adjust zero
        """
        for key in ref.keys():
            if key in self._obj.columns:
                self._obj[key] -= ref[key]
        return self._obj
    
    def get_valid_range(self, filter_criteria=(.1, 3) , vote: None|tuple[int, float] = [5, 0.8], valid_range: None|tuple[float, float] = None):
        """
        Get valid range
        Parameters
        ----------
        filter_criteria: tuple
            Filter criteria
        vote: tuple
            Vote criteria
        valid_range: tuple
            Valid range
        """
        if 'Range[m]' not in self._obj.columns:
            raise MissingSignalError('Range[m]')
        
        if filter_criteria:
            target = self._obj.hlm.butterworth_filter(filter_criteria[0], filter_criteria[1], cols=['Range[m]'], inplace=False)
        else:
            target = self._obj

        v_mat = np.zeros((len(target), vote[0]))
        for d in range(1, vote[0]+1):
            v_mat[:, d-1] = (target['Range[m]'].diff(periods=d)<0).astype(int)

        self._obj['In_range'] = (v_mat.mean(axis=1)>vote[1]).astype(int)
        s = self._obj['In_range']
        if (s == 1).any():
            last_one_idx = s[s == 1].index[-1]
            sub_signal = s.loc[:last_one_idx]
            zeros_before_last_pulse = sub_signal.index[sub_signal == 0]
            
            if not zeros_before_last_pulse.empty:
                last_zero_before_pulse = zeros_before_last_pulse[-1]
                self._obj.loc[:last_zero_before_pulse, ['In_range']] = 0

        if valid_range:
            self._obj['In_range'] = self._obj['In_range'].where(self._obj['Range[m]'].between(*valid_range), 0)
        return self._obj
    
    def get_in_range(self, cols=[], test_range : None|tuple[float, float] = None):
        """
        Keep only values if Range[m] is in range, set to nan otherwise
        Parameters
        ----------
        cols: list
            List of columns to get
        test_range: tuple
            Test range
        Return
        ----------
        new pd.DataFrame
        """
        for col in ['Range[m]', 'In_range']:
            if col not in self._obj.columns:
                raise MissingSignalError(col)

        target = self._obj[[col for col in cols if col in self._obj.columns]]
        if test_range:
            c = self._obj['Range[m]'].between(*test_range) * self._obj['In_range']
            return target.where(c.astype(bool), np.nan)
        else:
            return target.where(self._obj['In_range'].astype(bool), np.nan)

class TestPostProcess:
    def __init__(self):
        self.meta = {}
        self.root = ''
    
    def load_meta(self):
        if not os.path.exists(self.root / 'metadata.json'):
            return PostProcessCode.MISSING_METADATA_FILE
        if not Catalog.is_loaded():
            return PostProcessCode.MISSING_TEST_CATALOG_FILE
        with open(self.root / 'metadata.json', 'r') as f:
            self.meta = json.load(f)
        Catalog.set_scenario(self.meta.get('scenario_config_number', None))
        if not Catalog.is_scenario_set():
            return PostProcessCode.SCENARIO_NOT_SPECIFIED
        return PostProcessCode.SUCCESS
    
    def save_meta(self):
        if not self.meta:
            return PostProcessCode.MISSING_METADATA_FILE
        with open(self.root / 'metadata.json', 'w') as f:
            json.dump(self.meta, f, indent=2)
        return PostProcessCode.SUCCESS
    
    def load_files(self):
        if not self.meta:
            return PostProcessCode.MISSING_METADATA_FILE
        for tag in ['DAQ', 'NCOM', 'RCOM']:
            if not os.path.exists(self.meta.get(tag, "")):
                return PostProcessCode.FILE_NOT_FOUND
            
        self.daq = pd.read_csv(self.meta['DAQ'])
        if self.daq.empty:
            return PostProcessCode.FILE_EMPTY
        self.ncom = pd.read_csv(self.meta['NCOM'])
        if self.ncom.empty:
            return PostProcessCode.FILE_EMPTY
        self.rcom = pd.read_csv(self.meta['RCOM'])
        if self.rcom.empty:
            return PostProcessCode.FILE_EMPTY
        return PostProcessCode.SUCCESS
    
    def load(self, root):
        self.root = pathlib.Path(root)
        res = self.load_meta()
        if res != PostProcessCode.SUCCESS:
            raise PostProcessingError(res)
        res = self.load_files()
        if res != PostProcessCode.SUCCESS:
            raise PostProcessingError(res)
        return PostProcessCode.SUCCESS

    def clean(self):
        # Clean RCOM -> Keep only target 1 range in m, reindex
        self.rcom.hlm.reindex()
        self.rcom = self.rcom.hlm.filter('Target_number', 1)
        self.rcom.hlm.norm2(cols=['Lateral_range[m]', 'Longitudinal_range[m]'], col_name='Range[m]')
        self.rcom.hlm.keep(['Range[m]'])
        logger.logger.info("RCOM pre-processing done")

        # Clean NCOM -> Reindex, get lat/lon, get grade
        self.ncom.hlm.reindex()
        self.ncom.hlm.norm2(['North_velocity[m/s]','East_velocity[m/s]','Down_velocity[m/s]'], 'Velocity[m/s]')
        self.ncom.hlm.from_latlon().hlm.get_grade()
        self.ncom.drop(columns=['Time[s]'], inplace=True)
        logger.logger.info("NCOM pre-processing done")

        # Append RCOM to NCOM
        self.ncom = self.ncom.hlm.add(self.rcom)
        logger.logger.info("RCOM appended to NCOM")

        # Clean DAQ
        self.daq.hlm.reindex()
        self.daq.drop(
            columns=[col for col in self.daq.columns if col.startswith('Null')], 
            inplace=True)
        self.daq.hlm.fix_lux().hlm.butterworth_filter(cutoff=35, order=3)
        logger.logger.info("DAQ pre-processing done")

        # Append RCOM to DAQ
        self.daq = self.daq.hlm.add(self.rcom)
        logger.logger.info("RCOM appended to DAQ")
        return PostProcessCode.SUCCESS
    
    def validate(self):
        Catalog.set_prefix('scenario_info')

        # Range validation 
        range_ = (Catalog.get('Measurement Distance Range (m).min'),
                    Catalog.get('Measurement Distance Range (m).max'))
        
        self.ncom.hlm.get_valid_range(valid_range=range_)
        self.daq.hlm.get_valid_range(valid_range=range_)

        if SAVE_CSV:
            os.makedirs(self.root / 'result', exist_ok=True)
            self.ncom.to_csv(self.root / 'result' / 'ncom_processed.csv')
            self.daq.to_csv(self.root / 'result' / 'daq_processed.csv')
        
        tmp = self.ncom.hlm.get_in_range(cols=['Range[m]'])
        conditions = [
            abs(tmp.min()['Range[m]']-range_[0]) <= .5,
            abs(tmp.max()['Range[m]']-range_[1]) <= .5
        ]
        if not all(conditions):
            msg = f"Range requirements not met! Captured: ({tmp.min()}, {tmp.max()}), Required: ({range_[0]}, {range_[1]})/+-1"
            raise PostProcessingError(PostProcessCode.TEST_INVALID, msg)
        
        tmp = self.daq.hlm.get_in_range(cols=['Range[m]'])
        conditions = [
            abs(tmp.min()['Range[m]']-range_[0]) <= .5,
            abs(tmp.max()['Range[m]']-range_[1]) <= .5
        ]
        if not all(conditions):
            msg = f"Range requirements not met! Captured: ({tmp.min()}, {tmp.max()}), Required: ({range_[0]}, {range_[1]})/+-1"
            raise PostProcessingError(PostProcessCode.TEST_INVALID, msg)
        logger.logger.info("Range validation passed")
        
        
        # Speed validation
        range_ = (Catalog.get('Test Vehicle Speed (mph).min'),
                  Catalog.get('Test Vehicle Speed (mph).max'))
        coeff = 2.236936 # mps to mph
        tmp = self.ncom.hlm.get_in_range(cols=['Velocity[m/s]'])*coeff
        conditions = [
            tmp.min()['Velocity[m/s]'] >= range_[0],
            tmp.max()['Velocity[m/s]'] <= range_[1],
            tmp.max()['Velocity[m/s]']-tmp.min() <= 2
        ]
        if not all(conditions):
            msg = f"Speed requirements not! Captured: ({tmp.min()}, {tmp.max()}), Required: ({range_[0]}, {range_[1]})"
            raise PostProcessingError(PostProcessCode.TEST_INVALID, msg)
        logger.logger.info("Speed validation passed")

        #Radius of curvature validation
        if Catalog.get('Radius of Curve (m.)') != 'Straight':
            range_ = (Catalog.get('Radius of Curve (m.)').min,
                      Catalog.get('Radius of Curve (m.)').max)
            tmp = self.ncom.hlm.get_get_curve_radius()
            conditions = [
                tmp.min() >= range_[0],
                tmp.max() <= range_[1],
            ]
            if not all(conditions):
                msg = f"Radius of curvature requirements not met! Captured: ({tmp.min()}, {tmp.max()}), Required: ({range_[0]}, {range_[1]})"
                raise PostProcessingError(PostProcessCode.TEST_INVALID, msg)
            logger.logger.info("Radius of curvature validation passed")    
        return PostProcessCode.SUCCESS
    
    def examine(self):
        result = PostProcessCode.TEST_PASSED
        Catalog.set_prefix('lux_requirements')
        channels = Catalog.get('channels')
        cats = Catalog.get('illuminance_distance_intervals')
        for cat in cats:
            range_ = (cat.get('min_distance_m'),
                      cat.get('max_distance_m'))
            max_, soft_max_ = cat.get('max_illuminance_lux'), cat.get('max_illuminance_lux_plus_20pct')
            tmp = self.daq.hlm.get_in_range(cols=channels, test_range=range_).max()
            conditions = [tmp[col]<=max_ for col in channels]
            if all(conditions):
                logger.logger.info(f"Channels {[col for col in channels if tmp[col]>max_]} passed the max illuminance requirements in range ({range_[0]}, {range_[1]})")
                continue
            conditions = [tmp[col]<=soft_max_ for col in channels]
            if all(conditions):
                logger.logger.info(f"Channels {[col for col in channels if tmp[col]>soft_max_]} passed the soft max illuminance requirements in range ({range_[0]}, {range_[1]})")
                result = PostProcessCode.TEST_SOFT_PASSED
            else:
                msg = f"Channels {[col for col in channels if tmp[col]>soft_max_]} exceeds the soft max illuminance requirements in range ({range_[0]}, {range_[1]})"
                raise PostProcessingError(PostProcessCode.TEST_FAILED, msg)
        return result
    
    def process(self, root):
        ret = PostProcessCode.UNKNOWN
        logger.logger.info(f"Post processing {root}")
        try:
            self.load(root)
            self.clean()
            self.validate()
            ret = self.examine()
        except PostProcessingError as e:
            ret = e
        except MissingSignalError as e:
            ret = e
        except:
            pass
        if self.meta:
            self.meta['result'] = ret.code.name
        self.save_meta()
        try: #v
            from nodes.adb_postprocess_ import run_adb_report
            run_adb_report(root, meta=self.meta)
        except Exception as e:
            logger.logger.error(f"ADB report failed: {e}") #v
        return

if __name__ == '__main__':
    import time
    tpp = TestPostProcess()
    ts_ = time.time()
    tpp.process('./tests/20260218T024')
    print(time.time()-ts_)
    


