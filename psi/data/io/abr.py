'''
Interface for reading tone-evoked ABR data generated by psiexperiment

Example
-------

.. code-block:: python

    from psi.data.io import abr
    filename = '20220601-1200 ID1234 abr_io'
    with abr.load(filename) as fh:
        epochs = fh.get_epochs()
        epochs_filtered = fh.get_epochs_filtered()

'''
import logging
log = logging.getLogger(__name__)

from functools import lru_cache, partialmethod, wraps
import json
import os.path
from pathlib import Path
import shutil
import re
from glob import glob
import hashlib
import pickle
import warnings

import bcolz
import numpy as np
import pandas as pd
from scipy import signal

from psi.util import PSIJsonEncoder
from . import Recording
from .bcolz_tools import repair_carray_size


# Max size of LRU cache
MAXSIZE = 1024


MERGE_PATTERN = \
    r'\g<date>-* ' \
    r'\g<experimenter> ' \
    r'\g<animal> ' \
    r'\g<ear> ' \
    r'\g<note> ' \
    r'\g<experiment>*'


def cache(f, name=None):
    import inspect
    s = inspect.signature(f)
    if name is None:
        name = f.__code__.co_name

    @wraps(f)
    def wrapper(self, *args, bypass_cache=False, refresh_cache=False, **kwargs):
        if bypass_cache:
            return f(self, *args, **kwargs)

        cb = kwargs.pop('cb', None)

        bound_args = s.bind(self, *args, **kwargs)
        bound_args.apply_defaults()
        cache_kwargs = dict(bound_args.arguments)
        cache_kwargs.pop('self')
        cache_kwargs.pop('cb')

        string = json.dumps(cache_kwargs, sort_keys=True, allow_nan=True,
                            cls=PSIJsonEncoder)
        uuid = hashlib.sha256(string.encode('utf8')).hexdigest()

        cache_path = self.base_path / 'cache'
        cache_path.mkdir(parents=True, exist_ok=True)
        cache_file = cache_path / f'{name}-{uuid}-result.pkl'
        kwargs_cache_file = cache_path / f'{name}-{uuid}-kwargs.pkl'

        result = None
        try:
            if not refresh_cache and cache_file.exists():
                result = pd.read_pickle(cache_file)
                with open(kwargs_cache_file, 'rb') as fh:
                    cache_kwargs = pickle.load(fh)
                    if cache_kwargs != kwargs:
                        raise ValueError('Cache is corrupted')
        except:
            # Cache is corrupted. Delete it.
            cache_file.unlink()

        if result is None:
            result = f(self, *args, cb=cb, **kwargs)
            try:
                result.to_pickle(cache_file)
                with open(kwargs_cache_file, 'wb') as fh:
                    pickle.dump(kwargs, fh)
            except OSError:
                warnings.warn(f'Unable to create cache file at {cache_path}')

        return result

    return wrapper


class ABRFile(Recording):
    '''
    Wrapper around an ABR file with methods for loading and querying data

    Parameters
    ----------
    base_path : string
        Path to folder containing ABR data
    '''

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        try:
            getattr(self, 'eeg')
        except AttributeError:
            raise ValueError('EEG data missing')
        try:
            getattr(self, 'erp_metadata')
        except AttributeError:
            raise ValueError('ERP metadata missing')

    def get_setting(self, setting_name):
        '''
        Return value for setting

        Parameters
        ----------
        setting_name : string
            Setting to extract

        Returns
        -------
        object
            Value of setting

        Raises
        ------
        ValueError
            If the setting is not identical across all trials.
        KeyError
            If the setting does not exist.
        '''
        values = np.unique(self.erp_metadata[setting_name])
        if len(values) != 1:
            raise ValueError('{name} is not unique across all epochs.')
        return values[0]

    def get_setting_default(self, setting_name, default):
        '''
        Return value for setting

        Parameters
        ----------
        setting_name : string
            Setting to extract
        default : obj
            Value to return if setting doesn't exist.

        Returns
        -------
        object
            Value of setting

        Raises
        ------
        ValueError
            If the setting is not identical across all trials.
        '''
        try:
            return self.get_setting(setting_name)
        except KeyError:
            return default

    @property
    @lru_cache(maxsize=MAXSIZE)
    def eeg(self):
        '''
        Continuous EEG signal in `BcolzSignal` format.
        '''
        if 'eeg' in self.carray_names:
            # Load and ensure that the EEG data is fine. If not, repair it and
            # reload the data.
            rootdir = self.base_path / 'eeg'
            eeg = bcolz.carray(rootdir=rootdir)
            if len(eeg) == 0:
                log.debug('EEG for %s is corrupt. Repairing.', self.base_path)
                repair_carray_size(rootdir)
        return self.__getattr__('eeg')

    @property
    @lru_cache(maxsize=MAXSIZE)
    def erp_metadata(self):
        '''
        Raw ERP metadata in DataFrame format

        There will be one row for each epoch and one column for each parameter
        from the ABR experiment. For simplicity, all parameters beginning with
        `target_tone_` have that string removed. For example,
        `target_tone_frequency` will become `frequency`).
        '''
        data = self._load_bcolz_table('erp_metadata')
        return data.rename(columns=lambda x: x.replace('target_tone_', ''))

    @cache
    def get_epochs(self, offset=0, duration=8.5e-3, detrend='constant',
                   downsample=None, reject_threshold=None,
                   reject_mode='absolute', columns='auto', averages=None,
                   cb=None):
        '''
        Extract event-related epochs from EEG

        Parameters
        ----------
        {common_docstring}
        {epochs_docstring}
        '''
        fn = self.eeg.get_epochs
        result = fn(self.erp_metadata, offset, duration, detrend,
                    downsample=downsample, columns=columns, cb=cb)
        result = self._apply_reject(result, reject_threshold, reject_mode)
        result = self._apply_n(result, averages)
        return result

    @cache
    def get_random_segments(self, n, offset=0, duration=8.5e-3,
                            detrend='constant', downsample=None,
                            reject_threshold=None, reject_mode='absolute'):
        '''
        Extract random segments from filtered EEG

        Parameters
        ----------
        n : int
            Number of segments to return
        {common_docstring}
        '''
        fn = self.eeg.get_random_segments
        result = fn(n, offset, duration, detrend, downsample=downsample)
        return self._apply_reject(result, reject_threshold, reject_mode)

    @cache
    def get_epochs_filtered(self, filter_lb=300, filter_ub=3000,
                            filter_order=1, offset=-1e-3, duration=10e-3,
                            detrend='constant', pad_duration=10e-3,
                            downsample=None, reject_threshold=None,
                            reject_mode='absolute', columns='auto',
                            averages=None, cb=None):
        '''
        Extract event-related epochs from filtered EEG

        Parameters
        ----------
        {filter_docstring}
        {common_docstring}
        {epochs_docstring}
        '''
        fn = self.eeg.get_epochs_filtered
        result = fn(md=self.erp_metadata, offset=offset, duration=duration,
                    filter_lb=filter_lb, filter_ub=filter_ub,
                    filter_order=filter_order, detrend=detrend,
                    pad_duration=pad_duration, downsample=downsample,
                    columns=columns, cb=cb)
        result = self._apply_reject(result, reject_threshold, reject_mode)
        result = self._apply_n(result, averages)
        return result

    @cache
    def get_random_segments_filtered(self, n, filter_lb=300, filter_ub=3000,
                                     filter_order=1, offset=-1e-3,
                                     duration=10e-3, detrend='constant',
                                     pad_duration=10e-3,
                                     downsample=None,
                                     reject_threshold=None,
                                     reject_mode='absolute'):
        '''
        Extract random segments from EEG

        Parameters
        ----------
        n : int
            Number of segments to return
        {filter_docstring}
        {common_docstring}
        '''
        fn = self.eeg.get_random_segments_filtered
        result = fn(n, offset, duration, filter_lb, filter_ub, filter_order,
                    detrend, pad_duration, downsample=downsample)
        return self._apply_reject(result, reject_threshold, reject_mode)

    def _apply_reject(self, result, reject_threshold, reject_mode):
        result = result.dropna()

        if reject_threshold is None:
            # 'reject_mode' wasn't added until a later version of the ABR
            # program, so we set it to the default that was used before if not
            # present.
            reject_threshold = self.get_setting('reject_threshold')
            reject_mode = self.get_setting_default('reject_mode', 'absolute')

        if reject_threshold is not np.inf:
            # No point doing this if reject_threshold is infinite.
            if reject_mode == 'absolute':
                m = (result < reject_threshold).all(axis=1)
                result = result.loc[m]
            elif reject_mode == 'amplitude':
                # TODO
                raise NotImplementedError

        return result

    def _apply_n(self, result, averages):
        '''
        Limit epochs to the specified number of averages
        '''
        if averages is np.inf:
            return result
        if averages is None:
            averages = self.erp_metadata.loc[0, 'averages']

        grouping = list(result.index.names)
        grouping.remove('t0')
        if 'polarity' in result.index.names:
            n = averages // 2
            if (n * 2) != averages:
                m = f'Number of averages {averages} not divisible by 2'
                raise ValueError(m)
        else:
            n = averages
        return result.groupby(grouping, group_keys=False) \
            .apply(lambda x: x.iloc[:n])


class ABRSupersetFile:

    def __init__(self, *base_paths):
        self._fh = [ABRFile(base_path) for base_path in base_paths]

    def _merge_results(self, fn_name, *args, merge_on_file=False, **kwargs):
        result_set = [getattr(fh, fn_name)(*args, **kwargs) for fh in self._fh]
        if merge_on_file:
            return pd.concat(result_set, keys=range(len(self._fh)), names=['file'])
        offset = 0
        for result in result_set:
            t0 = result.index.get_level_values('t0')
            if offset > 0:
                result.index = result.index.set_levels(t0 + offset, 't0')
            offset += t0.max() + 1
        return pd.concat(result_set)

    get_epochs = partialmethod(_merge_results, 'get_epochs')
    get_epochs_filtered = partialmethod(_merge_results, 'get_epochs_filtered')
    get_random_segments = partialmethod(_merge_results, 'get_random_segments')
    get_random_segments_filtered = \
        partialmethod(_merge_results, 'get_random_segments_filtered')

    @classmethod
    def from_pattern(cls, base_path):
        head, tail = os.path.split(base_path)
        glob_tail = FILE_RE.sub(MERGE_PATTERN, tail)
        glob_pattern = os.path.join(head, glob_tail)
        folders = glob(glob_pattern)
        inst = cls(*folders)
        inst._base_path = base_path
        return inst

    @classmethod
    def from_folder(cls, base_path):
        folders = [os.path.join(base_path, f) \
                   for f in os.listdir(base_path)]
        inst = cls(*[f for f in folders if os.path.isdir(f)])
        inst._base_path = base_path
        return inst

    @property
    def erp_metadata(self):
        result_set = [fh.erp_metadata for fh in self._fh]
        return pd.concat(result_set, keys=range(len(self._fh)), names=['file'])


def list_abr_experiments(base_path):
    if is_abr_experiment(base_path, allow_superset=False):
        return [base_path]

    experiments = []
    base_path = Path(base_path)
    if base_path.is_file():
        return experiments

    for path in Path(base_path).iterdir():
        if path.is_dir():
            experiments.extend(list_abr_experiments(path))
    return experiments


def load(base_path, allow_superset=False):
    '''
    Load ABR data

    Parameters
    ----------
    base_path : string
        Path to folder
    allow_superset : bool
        If True, will merge all subfolders containing valid ABR data into a
        single superset ABR file. If False, base_path must be a valid ABR
        dataset.

    Returns
    -------
    {ABRFile, ABRSupersetFile}
        Depending on folder, will return either an instance of `ABRFile` or
        `ABRSupersetFile`.
    '''
    # This supports backwards compatibility
    check = os.path.join(base_path, 'erp_metadata')
    check_csv = os.path.join(base_path, 'erp_metadata.csv')
    if os.path.exists(check) or os.path.exists(check_csv):
        return ABRFile(base_path)
    if allow_superset:
        return ABRSupersetFile.from_folder(base_path)
    raise IOError(f'{base_path} is not an ABR dataset')


def is_abr_experiment(base_path, allow_superset=False):
    '''
    Checks if path contains valid ABR data

    Parameters
    ----------
    base_path : string
        Path to folder

    Returns
    -------
    bool
        True if path contains valid ABR data, False otherwise. If path doesn't
        exist, False is returned.
    '''
    try:
        result = load(base_path, allow_superset)
        return True
    except Exception as e:
        return False


filter_docstring = '''
        filter_lb : float
            Lower bound of filter passband, in Hz.
        filter_ub : float
            Upper bound of filter passband, in Hz.
        filter_order : int
            Filter order. Note that the effective order will be double this
            since we use zero-phase filtering.
'''.strip()


common_docstring = '''
        offset : float
            Starting point of epoch, in seconds re. trial start. Can be
            negative to capture prestimulus baseline.
        duration : float
            Duration of epoch, in seconds, relative to offset.
        detrend : {'constant', 'linear', None}
            Method for detrending
        pad_duration : float
            Duration, in seconds, to pad epoch prior to filtering. The extra
            samples will be discarded after filtering.
        reject_threshold : {None, float}
            If None, use the value stored in the file. Otherwise, use the
            provided value. To return all epochs, use `np.inf`.
        reject_mode : string
            Not imlemented
        cb : {None, callable}
            If a callable is provided, this will be called with the current
            fraction of segments loaded from the file. This is useful when
            loading many segments over a slow connection.
'''.strip()


epochs_docstring = '''
        columns : {'auto', list of names}
            Columns to include
        averages : None
            Limits the number of epochs returned to the number of averages
            specified. If None, use the value stored in the file. Otherwise,
            use the provided value. To return all epochs, use `np.inf`. For
            dual-polarity data, care will be taken to ensure the number of
            trials from each polarity match (even when set to `np.inf`).
        bypass_cache : bool
            If true, skip cache mechanism entirely. This also prevents a cache
            file from being saved.
        refresh_cache : bool
            If true, recompute from raw EEG data. If false and data has already
            been cached, return cached results.
'''.strip()


def format_docstrings(klass):
    fmt = {
        'common_docstring': common_docstring,
        'filter_docstring': filter_docstring,
        'epochs_docstring': epochs_docstring,
    }
    for member_name in dir(klass):
        member = getattr(klass, member_name)
        try:
            member.__doc__ = member.__doc__.format(**fmt)
        except:
            pass


format_docstrings(ABRFile)
