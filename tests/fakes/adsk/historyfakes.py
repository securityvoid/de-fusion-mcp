"""Fake feature, extent, definition and edge-set classes for the history tool's recipe dump. They are
plain attribute bags: tests set the properties a mapping row reads. Imported into ``adsk.fusion``."""
from .core import _Fake
from .fusion import ConstructionAxis, ConstructionPlane, Feature  # noqa: F401


class _Attrs(_Fake):
    def __init__(self, **kw):
        self.__dict__.update(kw)


# Features (the class name is what ``objectType`` reports).
class RevolveFeature(Feature):
    pass


class FilletFeature(Feature):
    pass


class ChamferFeature(Feature):
    pass


class ShellFeature(Feature):
    pass


class RectangularPatternFeature(Feature):
    pass


class CircularPatternFeature(Feature):
    pass


class MirrorFeature(Feature):
    pass


class CombineFeature(Feature):
    pass


class HoleFeature(Feature):
    pass


class ThreadFeature(Feature):
    pass


class MoveFeature(Feature):
    pass


class CoilFeature(Feature):
    pass


# Extent definitions, edge sets, construction definitions, hole positions (attribute bags).
class DistanceExtentDefinition(_Attrs):
    pass


class AngleExtentDefinition(_Attrs):
    pass


class SymmetricExtentDefinition(_Attrs):
    pass


class ConstantRadiusFilletEdgeSet(_Attrs):
    pass


class VariableRadiusFilletEdgeSet(_Attrs):
    pass


class EqualDistanceChamferEdgeSet(_Attrs):
    pass


class ConstructionPlaneOffsetDefinition(_Attrs):
    pass


class ConstructionPlaneAtAngleDefinition(_Attrs):
    pass


class ConstructionPlaneMidplaneDefinition(_Attrs):
    pass


class ConstructionAxisByLineDefinition(_Attrs):
    pass


class ConstructionAxisTwoPlaneDefinition(_Attrs):
    pass


class PointHolePositionDefinition(_Attrs):
    pass


class ThreadInfo(_Attrs):
    pass


class ArrayMatrix(_Fake):
    """A transform exposing ``asArray()`` (16 values, row-major, translation in 3, 7, 11)."""

    def __init__(self, values):
        self._values = list(values)

    def asArray(self):
        return list(self._values)


class HoleTypes:
    SimpleHoleType = 0
    CounterboreHoleType = 1
    CountersinkHoleType = 2


class PatternDistanceType:
    SpacingPatternDistanceType = 0
    ExtentPatternDistanceType = 1
