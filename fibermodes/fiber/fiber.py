
from . import geometry
from . import solver
from .solver.solver import FiberSolver
from math import sqrt, isnan, isinf
from fibermodes import Wavelength, Mode, ModeFamily
from fibermodes import constants
from fibermodes.functions import derivative
from itertools import count
import logging
from scipy.optimize import fixed_point


class MaxIterationsReachedWarning(UserWarning):

    """

    """
    pass


class Fiber(object):

    logger = logging.getLogger(__name__)

    def __init__(self, r, f, fp, m, mp, names, Cutoff=None, Neff=None):

        self._r = r
        self._names = names

        self.layers = []
        for f_, fp_, m_, mp_ in zip(f, fp, m, mp):
            layer = geometry.__dict__[f_](*fp_, m=m_, mp=mp_)
            self.layers.append(layer)

        self.co_cache = {Mode("HE", 1, 1): 0,
                         Mode("LP", 0, 1): 0}
        self.ne_cache = {}

        self.setSolvers(Cutoff, Neff)

    def __len__(self):
        return len(self.layers)

    def __str__(self):
        s = "Fiber {\n"
        for i, layer in enumerate(self.layers):
            geom = str(layer)
            radius = self.outerRadius(i)
            radius = '' if isinf(radius) else ' {:.3f} µm'.format(radius*1e6)
            name = self.name(i)
            name = ' "{}"'.format(name) if name else ''
            s += "    {}{}{}\n".format(geom, radius, name)
        s += "}"
        return s

    def fixedMatFiber(self, wl):
        f = []
        fp = []
        m = []
        mp = []
        for layer in self.layers:
            f.append(layer.__class__.__name__)
            fp.append(layer._fp)
            m.append("Fixed")
            mp.append([layer._m.n(wl, *layer._mp)])
        return Fiber(self._r, f, fp, m, mp, self._names,
                     self._cutoff.__class__, self._neff.__class__)

    def name(self, layer):
        return self._names[layer]

    def _layer(self, r):
        r = abs(r)
        for i, r_ in enumerate(self._r):
            if r < r_:
                return self.layers[i]
        return self.layers[-1]

    def innerRadius(self, layer):
        if layer < 0:
            layer = len(self._r) + layer + 1
        return self._r[layer-1] if layer else 0

    def outerRadius(self, layer):
        return self._r[layer] if layer < len(self._r) else float("inf")

    def thickness(self, layer):
        return self.outerRadius(layer) - self.innerRadius(layer)

    def index(self, r, wl):
        return self._layer(r).index(r, wl)

    def minIndex(self, layer, wl):
        return self.layers[layer].minIndex(wl)

    def maxIndex(self, layer, wl):
        return self.layers[layer].maxIndex(wl)

    def _findCutoffSolver(self):
        cutoff = FiberSolver
        if all(isinstance(layer, geometry.StepIndex)
               for layer in self.layers):
            nlayers = len(self)
            if nlayers == 2:  # SSIF
                cutoff = solver.ssif.Cutoff
            elif nlayers == 3:
                cutoff = solver.tlsif.Cutoff
        return cutoff

    def _findNeffSolver(self):
        neff = FiberSolver
        if all(isinstance(layer, geometry.StepIndex)
               for layer in self.layers):
            nlayers = len(self)
            if nlayers == 2:  # SSIF
                neff = solver.ssif.Neff
            elif nlayers == 3:
                neff = solver.mlsif.Neff
            else:
                neff = solver.mlsif.Neff
        return neff

    def setSolvers(self, Cutoff=None, Neff=None):
        assert Cutoff is None or issubclass(Cutoff, FiberSolver)
        assert Neff is None or issubclass(Neff, FiberSolver)
        if Cutoff is None:
            Cutoff = self._findCutoffSolver()
        self._cutoff = Cutoff(self)
        if Neff is None:
            Neff = self._findNeffSolver()
        self._neff = Neff(self)

    def set_ne_cache(self, wl, mode, neff):
        try:
            self.ne_cache[wl][mode] = neff
        except KeyError:
            self.ne_cache[wl] = {mode: neff}

    def NA(self, wl):
        n1 = max(layer.maxIndex(wl) for layer in self.layers)
        n2 = self.minIndex(-1, wl)
        return sqrt(n1*n1 - n2*n2)

    def V0(self, wl):
        return Wavelength(wl).k0 * self.innerRadius(-1) * self.NA(wl)

    def toWl(self, V0, maxiter=50, tol=1e-15):
        """Convert V0 number to wavelength.

        An iterative method is used, since the index can be wavelength
        dependant.

        """
        if V0 == 0:
            return float("inf")

        b = self.innerRadius(-1)
        wl = constants.tpi / V0 * b * self.NA(1.55e-6)

        def f(x):
            return constants.tpi / V0 * b * self.NA(x)

        if abs(wl - f(wl)) > tol:
            wl = fixed_point(f, wl)

        return Wavelength(wl)

    def cutoff(self, mode, delta=0.25):
        try:
            return self.co_cache[mode]
        except KeyError:
            co = self._cutoff(mode, delta)
            self.co_cache[mode] = co
            return co

    def cutoffWl(self, mode, delta=0.25):
        return self.toWl(self.cutoff(mode, delta))

    def neff(self, mode, wl, delta=1e-6, lowbound=None):
        try:
            return self.ne_cache[wl][mode]
        except KeyError:
            neff = self._neff(wl, mode, delta, lowbound)
            self.set_ne_cache(wl, mode, neff)
            return neff

    def beta(self, omega, mode, p=0, delta=1e-6, lowbound=None):
        wl = Wavelength(omega=omega)
        if p == 0:
            neff = self.neff(mode, wl, delta, lowbound)
            return neff * wl.k0

        m = 5
        j = (m - 1) // 2
        h = 1e5
        lb = lowbound
        for i in range(m-1, -1, -1):
            # Precompute neff using previous wavelength
            o = omega + (i-j) * h
            wl = Wavelength(omega=o)
            lb = self.neff(mode, wl, delta, lb) + delta * 1.1

        return derivative(
            self.beta, omega, p, m, j, h, mode, 0, delta, lowbound)

    def b(self, mode, wl, delta=1e-6, lowbound=None):
        """Normalized propagation constant"""
        neff = self.neff(mode, wl, delta, lowbound)
        nmax = max(layer.maxIndex(wl) for layer in self.layers)
        ncl = self.minIndex(-1, wl)
        ncl2 = ncl*ncl
        return (neff*neff - ncl2) / (nmax*nmax - ncl2)

    def vp(self, mode, wl, delta=1e-6, lowbound=None):
        return constants.c / self.neff(mode, wl, delta, lowbound)

    def ng(self, mode, wl, delta=1e-6, lowbound=None):
        return self.beta(Wavelength(wl).omega,
                         mode, 1, delta, lowbound) * constants.c

    def vg(self, mode, wl, delta=1e-6, lowbound=None):
        return 1 / self.beta(
            Wavelength(wl).omega, mode, 1, delta, lowbound)

    def D(self, mode, wl, delta=1e-6, lowbound=None):
        return -(self.beta(
                    Wavelength(wl).omega, mode, 2, delta, lowbound) *
                 constants.tpi * constants.c * 1e6 / (wl * wl))

    def S(self, mode, wl, delta=1e-6, lowbound=None):
        return (self.beta(
                    Wavelength(wl).omega, mode, 3, delta, lowbound) *
                (constants.tpi * constants.c / (wl * wl))**2 * 1e-3)

    def findVmodes(self, wl, numax=None, mmax=None, delta=1e-6):
        families = (ModeFamily.HE, ModeFamily.EH, ModeFamily.TE, ModeFamily.TM)
        return self.findModes(families, wl, numax, mmax, delta)

    def findLPmodes(self, wl, ellmax=None, mmax=None, delta=1e-6):
        families = (ModeFamily.LP,)
        return self.findModes(families, wl, ellmax, mmax, delta)

    def findModes(self, families, wl, numax=None, mmax=None, delta=1e-6):
        """Find all modes of given families, within given constraints



        """
        modes = set()
        v0 = self.V0(wl)
        for fam in families:
            for nu in count(0):
                try:
                    _mmax = mmax[nu]
                except IndexError:
                    _mmax = mmax[-1]
                except TypeError:
                    _mmax = mmax

                if (fam is ModeFamily.TE or fam is ModeFamily.TM) and nu > 0:
                    break
                if (fam is ModeFamily.HE or fam is ModeFamily.EH) and nu == 0:
                    continue
                if numax is not None and nu > numax:
                    break
                for m in count(1):
                    if _mmax is not None and m > _mmax:
                        break
                    mode = Mode(fam, nu, m)
                    try:
                        co = self.cutoff(mode)
                        if co > v0:
                            break
                    except (NotImplementedError, ValueError):
                        neff = self.neff(mode, wl, delta)
                        if isnan(neff):
                            break
                    modes.add(mode)
                if m == 1:
                    break
        return modes
