from datetime import datetime
from numpy import arange, array, einsum, rollaxis, searchsorted, sin
from time import strftime
from .constants import T0, DAY_S
from .framelib import ICRS_to_J2000 as B
from .nutationlib import compute_nutation
from .precessionlib import compute_precession

# Much of the following code is adapted from the USNO's "novas.c".

_half_second = 0.5 / DAY_S
_sequence = ['tdb', 'tt', 'ut1', 'utc']
_sequence_indexes = {name: i for i, name in enumerate(_sequence)}

extra_documentation = """

        This function takes a Julian date as argument. Either provide a
        `jd=` keyword argument supplying a `JulianDate` if you already
        have one, or supply a float or array of floats with one of the
        following keyword arguments:

        `tdb=` - Barycentric Dynamical Time
        `tt=`  - Terrestrial Time
        `ut1=` - Universal Time
        `utc=` - Coordinated Universal Time

"""

def takes_julian_date(function):
    """Wrap `function` so it accepts the standard Julian date arguments.

    A function that takes two arguments, `self` and `jd`, may be wrapped
    with this decorator if it wants to support optional auto-creation of
    its `jd` argument by accepting all of the same keyword arguments
    that the JulianDate constructor itself supports.

    """
    def wrapper(self, jd=None, utc=None, tai=None, tt=None,
                delta_t=0.0, cache=None):
        if jd is None:
            jd = JulianDate(utc=utc, tai=tai, tt=tt,
                            delta_t=delta_t, cache=cache)
        else:
            pass  # TODO: verify that they provided a JulianDate instance
        return function(self, jd)
    wrapper.__name__ = function.__name__
    synopsis, blank_line, description = function.__doc__.partition('\n\n')
    wrapper.__doc__ = ''.join(synopsis + extra_documentation + description)
    return wrapper

def _wrap(a):
    if hasattr(a, 'shape'):
        return a
    if hasattr(a, '__len__'):
        return array(a)
    return array((a,))

def _to_array(a):
    if not hasattr(a, 'shape'):
        a = array(a)
    return a

tt_minus_tai = array(32.184 / DAY_S)

class JulianDate(object):
    """Julian date.

    Attributes:

    `utc`     - Coordinated Universal Time
    `tai`     - International Atomic Time
    `tt`      - Terrestrial Time
    `delta_t` - Difference between Terrestrial Time and UT1
    `cache`   - SkyField `Cache` for automatically fetching `delta_t` table

    """
    def __init__(self, utc=None, tai=None, tt=None, delta_t=0.0, cache=None):

        self.delta_t = _to_array(delta_t)

        if cache is None:
            from skyfield.data import cache
        self.cache = cache

        if utc is not None:
            self.utc = utc
            if tai is None:
                leap_dates, leap_offsets = cache.run(usno_leapseconds)

                if isinstance(utc, datetime):
                    year = utc.year
                    month = utc.month
                    day = utc.day
                    hour = utc.hour
                    minute = utc.minute
                    second = utc.second + utc.microsecond * 1e-6
                else:
                    year, month, day, hour, minute, second = utc

                j = julian_date(year, month, day, hour, minute, 0.0)
                i = searchsorted(leap_dates, j, 'right')
                tai = j + (second + leap_offsets[i]) / DAY_S

        if tai is not None:
            self.tai = _to_array(tai)
            if tt is None:
                tt = tai + tt_minus_tai

        if tt is None:
            raise ValueError('you must supply either utc, tai, or tt when'
                             ' building a JulianDate')

        self.tt = _to_array(tt)
        self.shape = self.tt.shape
        self.delta_t = delta_t

    def dayrange(self, days, step=1.0):
        """Return a JulianDate extending `days` into the future."""
        cls = type(self)
        return cls(ut1=arange(self.ut1, self.ut1 + days + step * 0.5, step))

    def utc_iso(self, places=0):
        if places:
            power_of_ten = 10 ** places
            offset = _half_second / power_of_ten
            y, m, d, H, M, S = self._utc(offset)
            S, F = divmod(S, 1.0)
            return '%04d-%02d-%02dT%02d:%02d:%02d.%0*dZ' % (
                y, m, d, H, M, S, places, F * power_of_ten)
        else:
            tup = self._utc(_half_second)
            return '%04d-%02d-%02dT%02d:%02d:%02dZ' % tup

    def utc_strftime(self, format):
        tup = self._utc(_half_second)
        y, mon, d, h, m, s = tup
        tup = (int(y), int(mon), int(d), int(h), int(m), int(s), 0, 0, 0)
        return strftime(format, tup)

    def _utc(self, offset=0.0):
        """Return UTC as (year, month, day, hour, minute, second.fraction).

        The `offset` is added to the UTC time before it is split into
        its components.  This is useful if the user is going to round
        the result before displaying it.  If the result is going to be
        displayed as seconds, for example, set `offset` to half a second
        and then throw away the fraction; if the result is going to be
        displayed as minutes, set `offset` to thirty seconds and then
        throw away the seconds; and so forth.

        """
        tai = self.tai + offset
        leap_dates, leap_offsets = self.cache.run(usno_leapseconds)
        leap_reverse_dates = leap_dates + leap_offsets / DAY_S
        i = searchsorted(leap_reverse_dates, tai, 'right')
        j = tai - leap_offsets[i] / DAY_S
        whole, fraction = divmod(j + 0.5, 1.0)
        whole = int(whole)
        if i and j < leap_dates[i-1]:
            y, mon, d = calendar_date(whole)
            h, m, s = 23, 59, 60.0
        else:
            y, mon, d = calendar_date(whole)
            h, hfrac = divmod(fraction * 24.0, 1.0)
            m, s = divmod(hfrac * 3600.0, 60.0)
        self.utc = utc = (y, mon, d, h, m, s)
        return utc

    def __getattr__(self, name):

        # Cache of several expensive functions of time.

        if name == 'P':
            self.P = P = compute_precession(self.tdb)
            return P

        if name == 'PT':
            self.PT = PT = rollaxis(self.P, 1)
            return PT

        if name == 'N':
            self.N = N = compute_nutation(self)
            return N

        if name == 'NT':
            self.NT = NT = rollaxis(self.N, 1)
            return NT

        if name == 'M':
            self.M = M = einsum('ij...,jk...,kl...->il...', self.N, self.P, B)
            return M

        if name == 'MT':
            self.MT = MT = rollaxis(self.M, 1)
            return MT

        # Conversion between timescales.

        if name == 'tai':
            self.tai = tai = self.tt - tt_minus_tai
            return tai

        if name == 'utc':
            tai = self.tai
            leap_dates, leap_offsets = self.cache.run(usno_leapseconds)
            leap_reverse_dates = leap_dates + leap_offsets / DAY_S
            i = searchsorted(leap_reverse_dates, tai, 'right')
            j = tai - leap_offsets[i] / DAY_S
            y, mon, d, h = cal_date(j)
            h, hfrac = divmod(h, 1.0)
            m, s = divmod(hfrac * 3600.0, 60.0)
            self.utc = utc = (y, mon, d, h, m, s)
            return utc

        if name == 'tdb':
            tt = self.tt
            self.tdb = tdb = tt + tdb_minus_tt(tt) / DAY_S
            return tdb

        if name == 'ut1':
            self.ut1 = ut1 = self.tt - self.delta_t / DAY_S
            return ut1

        raise AttributeError('no such attribute %r' % name)


def julian_date(year, month=1, day=1, hour=0.0, minute=0.0, second=0.0):
    janfeb = month < 3
    return ((second / 60.0 + minute) / 60.0 + hour) / 24.0 - 0.5 + (
            day - 32075
            + 1461 * (year + 4800 - janfeb) // 4
            + 367 * (month - 2 + janfeb * 12) // 12
            - 3 * ((year + 4900 - janfeb) // 100) // 4
            )

def cal_date(jd):
    """Convert Julian Day `jd` into a Gregorian year, month, day, and hour."""
    jd = jd + 0.5

    hour = jd % 1.0 * 24.0
    k = int(jd) + 68569
    n = 4 * k // 146097;

    k = k - (146097 * n + 3) // 4
    m = 4000 * (k + 1) // 1461001
    k = k - 1461 * m // 4 + 31
    month = 80 * k // 2447
    day = k - 2447 * month // 80
    k = month // 11

    month = month + 2 - 12 * k
    year = 100 * (n - 49) + m + k

    return year, month, day, hour

def calendar_date(jd_integer):
    """Convert Julian Day `jd_integer` into a Gregorian (year, month, day)."""

    k = jd_integer + 68569
    n = 4 * k // 146097

    k = k - (146097 * n + 3) // 4
    m = 4000 * (k + 1) // 1461001
    k = k - 1461 * m // 4 + 31
    month = 80 * k // 2447
    day = k - 2447 * month // 80
    k = month // 11

    month = month + 2 - 12 * k
    year = 100 * (n - 49) + m + k

    return year, month, day

def tdb_minus_tt(jd_tdb):
    """Computes TT corresponding to a TDB Julian date."""

    t = (jd_tdb - T0) / 36525.0

    # Expression given in USNO Circular 179, eq. 2.6.

    return (0.001657 * sin ( 628.3076 * t + 6.2401)
          + 0.000022 * sin ( 575.3385 * t + 4.2970)
          + 0.000014 * sin (1256.6152 * t + 6.1969)
          + 0.000005 * sin ( 606.9777 * t + 4.0212)
          + 0.000005 * sin (  52.9691 * t + 0.4444)
          + 0.000002 * sin (  21.3299 * t + 5.5431)
          + 0.000010 * t * sin ( 628.3076 * t + 4.2490))

def usno_leapseconds(cache):
    """Download the USNO table of leap seconds as a ``(2, N+1)`` NumPy array.

    The array has two rows ``[leap_dates leap_offsets]``.  The first row
    is used to find where a given date ``jd`` falls in the table::

        index = np.searchsorted(leap_dates, jd, 'right')

    This can return a value from ``0`` to ``N``, allowing the
    corresponding UTC offset to be fetched with::

        offset = leap_offsets[index]

    The offset is the number of seconds that must be added to a UTC time
    to build the corresponding TAI time.

    """
    with cache.open_url('http://maia.usno.navy.mil/ser7/leapsec.dat') as f:
        lines = f.readlines()

    linefields = [line.split() for line in lines]
    dates = [float(fields[4]) for fields in linefields]
    offsets = [float(fields[6]) for fields in linefields]

    dates.append(float('inf'))
    offsets.insert(0, offsets[0])

    return array([dates, offsets])
