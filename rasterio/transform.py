"""Geospatial transforms"""

from collections.abc import Iterable
from functools import partial
import math
import sys

from affine import Affine

import rasterio._loading
with rasterio._loading.add_gdal_dll_directories():
    import rasterio
    from rasterio.env import env_ctx_if_needed
    from rasterio._transform import (
        _transform_from_gcps,
        RPCTransformerBase,
        GCPTransformerBase
    )
    from rasterio.enums import TransformDirection, TransformMethod
    from rasterio.control import GroundControlPoint
    from rasterio.rpc import RPC
    from rasterio.errors import TransformError

IDENTITY = Affine.identity()
GDAL_IDENTITY = IDENTITY.to_gdal()


class TransformMethodsMixin:
    """Mixin providing methods for calculations related
    to transforming between rows and columns of the raster
    array and the coordinates.

    These methods are wrappers for the functionality in
    `rasterio.transform` module.

    A subclass with this mixin MUST provide a `transform`
    property.
    """

    def xy(self, row, col, z=None, offset="center", transform_method=TransformMethod.affine, **rpc_options):
        """Returns the coordinates ``(x, y)`` of a pixel at `row` and `col`.
        The pixel's center is returned by default, but a corner can be returned
        by setting `offset` to one of `ul, ur, ll, lr`.

        Parameters
        ----------
        row : int
            Pixel row.
        col : int
            Pixel column.
        z : float, optional
            Height associated with coordinates. Primarily used for RPC based 
            coordinate transformations. Ignored for affine based 
            transformations. Default: 0.
        offset : str, optional
            Determines if the returned coordinates are for the center of the
            pixel or for a corner.
        transform_method: TransformMethod, optional 
            The coordinate transformation method. Default: `TransformMethod.affine`.
        rpc_options: dict, optional
            Additional arguments passed to GDALCreateRPCTransformer

        Returns
        -------
        tuple
            ``(x, y)``
        """
        transform = getattr(self, transform_method.value)
        if transform_method is TransformMethod.gcps:
            transform = transform[0]
        if not transform:
            raise AttributeError("Dataset has no {}".format(transform_method))
        return xy(transform, row, col, z=z, offset=offset, **rpc_options)

    def index(self, x, y, z=None, op=math.floor, precision=None, transform_method=TransformMethod.affine, **rpc_options):
        """
        Returns the (row, col) index of the pixel containing (x, y) given a
        coordinate reference system.

        Use an epsilon, magnitude determined by the precision parameter
        and sign determined by the op function: positive for floor, negative for ceil.

        Parameters
        ----------
        x : float
            x value in coordinate reference system
        y : float
            y value in coordinate reference system
        z : float, optional
            Height associated with coordinates. Primarily used for RPC based 
            coordinate transformations. Ignored for affine based 
            transformations. Default: 0.
        op : function, optional (default: math.floor)
            Function to convert fractional pixels to whole numbers (floor,
            ceiling, round)
        precision : int, optional (default: None)
            Decimal places of precision in indexing, as in `round()`.
        transform_method: TransformMethod, optional 
            The coordinate transformation method. Default: `TransformMethod.affine`.
        rpc_options: dict, optional
            Additional arguments passed to GDALCreateRPCTransformer

        Returns
        -------
        tuple
            (row index, col index)
        """
        transform = getattr(self, transform_method.value)
        if transform_method is TransformMethod.gcps:
            transform = transform[0]
        if not transform:
            raise AttributeError("Dataset has no {}".format(transform_method))
        return rowcol(transform, x, y, z=z, op=op, precision=precision, **rpc_options)

def get_transformer(transform, **rpc_options):
    """Return the appropriate transformer class"""
    if transform is None:
        raise ValueError("Invalid transform")
    if isinstance(transform, Affine):
        transformer_cls = partial(AffineTransformer, transform)
    elif isinstance(transform, RPC):
        transformer_cls = partial(RPCTransformer, transform, **rpc_options)
    else:
        transformer_cls = partial(GCPTransformer, transform)
    return transformer_cls

def tastes_like_gdal(seq):
    """Return True if `seq` matches the GDAL geotransform pattern."""
    return tuple(seq) == GDAL_IDENTITY or (
        seq[2] == seq[4] == 0.0 and seq[1] > 0 and seq[5] < 0)


def guard_transform(transform):
    """Return an Affine transformation instance."""
    if not isinstance(transform, Affine):
        if tastes_like_gdal(transform):
            raise TypeError(
                "GDAL-style transforms have been deprecated.  This "
                "exception will be raised for a period of time to highlight "
                "potentially confusing errors, but will eventually be removed.")
        else:
            transform = Affine(*transform)
    return transform


def from_origin(west, north, xsize, ysize):
    """Return an Affine transformation given upper left and pixel sizes.

    Return an Affine transformation for a georeferenced raster given
    the coordinates of its upper left corner `west`, `north` and pixel
    sizes `xsize`, `ysize`.
    """
    return Affine.translation(west, north) * Affine.scale(xsize, -ysize)


def from_bounds(west, south, east, north, width, height):
    """Return an Affine transformation given bounds, width and height.

    Return an Affine transformation for a georeferenced raster given
    its bounds `west`, `south`, `east`, `north` and its `width` and
    `height` in number of pixels.
    """
    return Affine.translation(west, north) * Affine.scale(
        (east - west) / width, (south - north) / height)


def array_bounds(height, width, transform):
    """Return the bounds of an array given height, width, and a transform.

    Return the `west, south, east, north` bounds of an array given
    its height, width, and an affine transform.
    """
    w, n = transform.xoff, transform.yoff
    e, s = transform * (width, height)
    return w, s, e, n


def xy(transform, rows, cols, zs=None, offset='center', **rpc_options):
    """Returns the x and y coordinates of pixels at `rows` and `cols`.

    The pixel's center is returned by default, but a corner can be returned
    by setting `offset` to one of `ul, ur, ll, lr`.

    Supports affine, Ground Control Point (GCP), or Rational Polynomial 
    Coefficients (RPC) based coordinate transformations.

    Parameters
    ----------
    transform : Affine or sequence of GroundControlPoint or RPC
        Transform suitable for input to AffineTransformer, GCPTransformer, or RPCTransformer. 
    rows : list or int
        Pixel rows.
    cols : int or sequence of ints
        Pixel columns.
    zs : list or float, optional
        Height associated with coordinates. Primarily used for RPC based 
        coordinate transformations. Ignored for affine based 
        transformations. Default: 0.
    offset : str, optional
        Determines if the returned coordinates are for the center of the
        pixel or for a corner.
    rpc_options : dict, optional
        Additional arguments passed to GDALCreateRPCTransformer.

    Returns
    -------
    xs : float or list of floats
        x coordinates in coordinate reference system
    ys : float or list of floats
        y coordinates in coordinate reference system

    """
    transformer_cls = get_transformer(transform, **rpc_options)
    with transformer_cls() as transformer:
        return transformer.xy(rows, cols, zs=zs, offset=offset)


def rowcol(transform, xs, ys, zs=None, op=math.floor, precision=None, **rpc_options):
    """
    Returns the rows and cols of the pixels containing (x, y) given a
    coordinate reference system.

    Use an epsilon, magnitude determined by the precision parameter
    and sign determined by the op function: positive for floor, negative for ceil.

    Parameters
    ----------
    transform : Affine or sequence of GroundControlPoint or RPC
        Transform suitable for input to AffineTransformer, GCPTransformer, or RPCTransformer.
    xs : list or float
        x values in coordinate reference system.
    ys : list or float
        y values in coordinate reference system.
    zs : list or float, optional
        Height associated with coordinates. Primarily used for RPC based 
        coordinate transformations. Ignored for affine based 
        transformations. Default: 0.
    op : function
        Function to convert fractional pixels to whole numbers (floor, ceiling,
        round).
    precision : int or float, optional
        An integer number of decimal points of precision when computing
        inverse transform, or an absolute float precision.
    rpc_options : dict, optional
        Additional arguments passed to GDALCreateRPCTransformer.

    Returns
    -------
    rows : list of ints
        list of row indices
    cols : list of ints
        list of column indices

    """
    transformer_cls = get_transformer(transform, **rpc_options)
    with transformer_cls() as transformer:
        return transformer.rowcol(xs, ys, zs=zs, op=op, precision=precision)


def from_gcps(gcps):
    """Make an Affine transform from ground control points.

    Parameters
    ----------
    gcps : sequence of GroundControlPoint
        Such as the first item of a dataset's `gcps` property.

    Returns
    -------
    Affine

    """
    return Affine.from_gdal(*_transform_from_gcps(gcps))

class TransformerBase():
    """
    Generic GDAL transformer base class

    Notes
    -----
    Subclasses must have a _transformer attribute and implement a `_transform` method.
    """
    def __init__(self):
        self._transformer = None
        self.closed = True

    def close(self):
        raise NotImplementedError
    
    def _ensure_arr_input(self, xs, ys, zs=None):
        """Ensure all input coordinates are mapped to array-like objects
        
        Raises
        ------
        TransformError
            If input coordinates are not all of the same length
        """
        if (isinstance(xs, Iterable) and not isinstance(ys, Iterable)) or (
            isinstance(ys, Iterable) and not isinstance(xs, Iterable)
        ):
            raise TransformError("Invalid inputs")
        if not isinstance(xs, Iterable) and not isinstance(ys, Iterable):
            xs = [xs]
            ys = [ys]
        if zs is None:
            zs = [0] * len(xs)
        elif not isinstance(zs, Iterable):
            zs = [zs]
        if len(set((len(xs), len(ys), len(zs)))) > 1:
            raise TransformError("Input coordinates must be of equal length")
        return xs, ys, zs

    def __enter__(self):
        self._env = env_ctx_if_needed()
        self._env.__enter__()
        return self

    def __exit__(self, type, value, traceback):
        self.close()
        self._env.__exit__()
    
    def rowcol(self, xs, ys, zs=None, op=math.floor, precision=None):
        """
        Returns rows and cols coordinates given geographic coordinates

        Parameters
        ----------
        xs, ys : float or list of float
            Geographic coordinates
        zs : float or list of float, optional
            Height associated with coordinates. Primarily used for RPC based 
            coordinate transformations. Ignored for affine based 
            transformations. Default: 0.
        op : function, optional (default: math.floor)
            Function to convert fractional pixels to whole numbers (floor,
            ceiling, round)
        precision : int, optional (default: None)
            Decimal places of precision in indexing, as in `round()`.
        

        Raises
        ------
        ValueError
            If input coordinates are not all equal length

        Returns
        -------
            tuple of float or list of float
        """
        AS_ARR = True if hasattr(xs, "__iter__") else False
        xs, ys, zs = self._ensure_arr_input(xs, ys, zs=zs)
        
        if precision is None:
            eps = sys.float_info.epsilon
        elif isinstance(precision, int):
            eps = 10.0 ** -precision
        else:
            eps = precision
        
        # If op rounds up, switch the sign of eps.
        if op(0.1) >= 1:
            eps = -eps
        f = lambda val: val + eps

        try:
            xs = list(map(f, xs))
            ys = list(map(f, ys))
            new_cols, new_rows = self._transform(
                xs, ys, zs, transform_direction=TransformDirection.reverse
            )

            if len(new_rows) == 1 and not AS_ARR:
                return (op(new_rows[0]), op(new_cols[0]))
            else:
                return ([op(r) for r in new_rows], [op(c) for c in new_cols])
        except TypeError:
            raise TransformError("Invalid inputs")

    def xy(self, rows, cols, zs=None, offset='center'):
        """
        Returns geographic coordinates given dataset rows and cols coordinates

        Parameters
        ----------
        rows, cols : int or list of int
            Image pixel coordinates
        zs : float or list of float, optional
            Height associated with coordinates. Primarily used for RPC based 
            coordinate transformations. Ignored for affine based 
            transformations. Default: 0.
        offset : str, optional
            Determines if the returned coordinates are for the center of the
            pixel or for a corner.
        Raises
        ------
        ValueError
            If input coordinates are not all equal length

        Returns
        -------
            tuple of float or list of float
        """
        AS_ARR = True if hasattr(rows, "__iter__") else False
        rows, cols, zs = self._ensure_arr_input(rows, cols, zs=zs)
        
        if offset == 'center':
            coff, roff = (0.5, 0.5)
        elif offset == 'ul':
            coff, roff = (0, 0)
        elif offset == 'ur':
            coff, roff = (1, 0)
        elif offset == 'll':
            coff, roff = (0, 1)
        elif offset == 'lr':
            coff, roff = (1, 1)
        else:
            raise TransformError("Invalid offset")
        
        # shift input coordinates according to offset
        T = IDENTITY.translation(coff, roff)
        temp_rows = []
        temp_cols = []
        try:
            for pt in zip(cols, rows):
                y, x = T * pt
                temp_rows.append(y)
                temp_cols.append(x)

            new_ys, new_xs = self._transform(
                temp_rows, temp_cols, zs, transform_direction=TransformDirection.forward
            )

            if len(new_ys) == 1 and not AS_ARR:
                return (new_ys[0], new_xs[0])
            else:
                return (new_ys, new_xs)
        except TypeError:
            raise TransformError("Invalid inputs")
    
    def _transform(self, xs, ys, zs, transform_direction):
        raise NotImplementedError


class AffineTransformer(TransformerBase):
    """A pure Python class related to affine based coordinate transformations."""
    def __init__(self, affine_transform):
        super().__init__()
        if not isinstance(affine_transform, Affine):
            raise ValueError("Not an affine transform")
        self._transformer = affine_transform

    def close(self):
        pass
    
    def _transform(self, xs, ys, zs, transform_direction):
        resxs = []
        resys = []
        
        if transform_direction is TransformDirection.forward:
            transform = self._transformer
        elif transform_direction is TransformDirection.reverse:
            transform = ~self._transformer

        for x, y in zip(xs, ys):
            resx, resy = transform * (x, y)
            resxs.append(resx)
            resys.append(resy)
        
        return (resxs, resys)
    
    def __repr__(self):
        return "<AffineTransformer>"


class RPCTransformer(RPCTransformerBase, TransformerBase):
    """
    Class related to Rational Polynomial Coeffecients (RPCs) based
    coordinate transformations.

    Uses GDALCreateRPCTransformer and GDALRPCTransform for computations. Options
    for GDALCreateRPCTransformer may be passed using `rpc_options`.
    Ensure that GDAL transformer objects are destroyed by calling `close()` 
    method or using context manager interface.
    """
    def __init__(self, rpcs, **rpc_options):
        if not isinstance(rpcs, (RPC, dict)):
            raise ValueError("RPCTransformer requires RPC")
        super().__init__(rpcs, **rpc_options)

    def __repr__(self):
        return "<{} RPCTransformer>".format(
            self.closed and 'closed' or 'open')


class GCPTransformer(GCPTransformerBase, TransformerBase):
    """
    Class related to Ground Control Point (GCPs) based
    coordinate transformations.

    Uses GDALCreateGCPTransformer and GDALGCPTransform for computations.
    Ensure that GDAL transformer objects are destroyed by calling `close()` 
    method or using context manager interface.
    """
    def __init__(self, gcps):
        if len(gcps) and not isinstance(gcps[0], GroundControlPoint):
            raise ValueError("GCPTransformer requires sequence of GroundControlPoint")
        super().__init__(gcps)

    def __repr__(self):
        return "<{} GCPTransformer>".format(
            self.closed and 'closed' or 'open')
