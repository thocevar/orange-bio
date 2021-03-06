# -*- coding: utf-8 -*-
"""
Differential Gene Expression
----------------------------

"""
import sys
import copy
from types import SimpleNamespace as namespace

import numpy as np
import scipy.stats
import scipy.special

from PyQt4 import QtGui, QtCore
from PyQt4.QtCore import Qt
from PyQt4.QtCore import pyqtSignal as Signal, pyqtSlot as Slot

import pyqtgraph as pg

import Orange.data
from Orange.preprocess import transformation

from Orange.widgets import widget, gui, settings

from Orange.widgets.utils.datacaching import data_hints
from Orange.widgets.utils import concurrent

from .utils import gui as guiutils
from .utils import group as grouputils
from .utils.settings import SetContextHandler


def score_fold_change(a, b, axis=0):
    """
    Calculate the fold change between `a` and `b` samples.

    Parameters
    ----------
    a, b : array
        Arrays containing the samples
    axis : int
        Axis over which to compute the FC

    Returns
    -------
    FC : array
        The FC scores
    """
    mean_a = np.nanmean(a, axis=axis)
    mean_b = np.nanmean(b, axis=axis)
    res = mean_a / mean_b
    warning = None
    if np.any(res < 0):
        res[res < 0] = float("nan")
        warning = "Negative fold change scores were ignored. You should use another scoring method."
    return res, warning


def score_log_fold_change(a, b, axis=0):
    """
    Return the log2(FC).

    See Also
    --------
    score_fold_change

    """
    s, w = score_fold_change(a, b, axis=axis) 
    return np.log2(s), w


def score_ttest(a, b, axis=0):
    T, P = scipy.stats.ttest_ind(a, b, axis=axis)
    return T, P


def score_ttest_t(a, b, axis=0):
    T, _ = score_ttest(a, b, axis=axis)
    return T


def score_ttest_p(a, b, axis=0):
    _, P = score_ttest(a, b, axis=axis)
    return P


def score_anova(*arrays, axis=0):
    F, P = f_oneway(*arrays, axis=axis)
    return F, P


def score_anova_(*arrays, axis=0):
    arrays = [np.asarray(arr, dtype=float) for arr in arrays]

    if not len(arrays) > 1:
        raise TypeError("Need at least 2 positional arguments")

    if not 0 <= axis < 2:
        raise ValueError("0 <= axis < 2")

    if not all(arrays[i].ndim == arrays[i + 1].ndim
               for i in range(len(arrays) - 2)):
        raise ValueError("All arrays must have the same number of dimensions")

    if axis >= arrays[0].ndim:
        raise ValueError()

    if axis == 0:
        arrays = [arr.T for arr in arrays]

    scores = [scipy.stats.f_oneway(*ars) for ars in zip(*arrays)]
    F, P = zip(*scores)
    return np.array(F, dtype=float), np.array(P, dtype=float)


def f_oneway(*arrays, axis=0):
    """
    Perform a 1-way ANOVA

    Like `scipy.stats.f_oneway` but accept 2D arrays, with `axis`
    specifying over which axis to operate (in which axis the samples
    are stored).

    Parameters
    ----------
    A1, A2, ... : array_like
        The samples for each group.
    axis : int
        The axis which contain the samples.

    Returns
    -------
    F : array
        F scores
    P : array
        P values

    See also
    --------
    scipy.stats.f_oneway
    """
    arrays = [np.asarray(a, dtype=float) for a in arrays]
    alldata = np.concatenate(arrays, axis)
    bign = alldata.shape[axis]
    sstot = np.sum(alldata ** 2, axis) - (np.sum(alldata, axis) ** 2) / bign

    ssarrays = [(np.sum(a, axis, keepdims=True) ** 2) / a.shape[axis]
                for a in arrays]
    ssbn = np.sum(np.concatenate(ssarrays, axis), axis)
    ssbn -= (np.sum(alldata, axis) ** 2) / bign
    assert sstot.shape == ssbn.shape

    sswn = sstot - ssbn
    dfbn = len(arrays) - 1
    dfwn = bign - len(arrays)
    msb = ssbn / dfbn
    msw = sswn / dfwn
    f = msb / msw
    prob = scipy.special.fdtrc(dfbn, dfwn, f)
    return f, prob


def score_anova_f(*arrays, axis=0):
    F, _ = score_anova(*arrays, axis=axis)
    return F


def score_anova_p(*arrays, axis=0):
    _, P = score_anova(*arrays, axis=axis)
    return P


def score_signal_to_noise(a, b, axis=0):
    mean_a = np.nanmean(a, axis=axis)
    mean_b = np.nanmean(b, axis=axis)

    std_a = np.nanstd(a, axis=axis, ddof=1)
    std_b = np.nanstd(b, axis=axis, ddof=1)

    return (mean_a - mean_b) / (std_a + std_b)


def score_mann_whitney(a, b, axis=0):
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)

    if not 0 <= axis < 2:
        raise ValueError("Axis")

    if a.ndim != b.ndim:
        raise ValueError

    if axis >= a.ndim:
        raise ValueError

    if axis == 0:
        a, b = a.T, b.T

    res = [scipy.stats.mannwhitneyu(a_, b_) for a_, b_ in zip(a, b)]
    U, P = zip(*res)
    return np.array(U), np.array(P)


def score_mann_whitney_u(a, b, axis=0):
    U, _ = score_mann_whitney(a, b, axis=axis)
    return U


class InfiniteLine(pg.InfiniteLine):
    def paint(self, painter, option, widget=None):
        brect = self.boundingRect()
        c = brect.center()
        line = QtCore.QLineF(brect.left(), c.y(), brect.right(), c.y())
        t = painter.transform()
        line = t.map(line)
        painter.save()
        painter.resetTransform()
        painter.setPen(self.currentPen)
        painter.drawLine(line)
        painter.restore()


class Histogram(pg.PlotWidget):
    """
    A histogram plot with interactive 'tail' selection
    """
    #: Emitted when the selection boundary has changed
    selectionChanged = Signal()
    #: Emitted when the selection boundary has been edited by the user
    #: (by dragging the boundary lines)
    selectionEdited = Signal()

    #: Selection mode
    NoSelection, Low, High, TwoSided, Middle = 0, 1, 2, 3, 4

    def __init__(self, parent=None, **kwargs):
        pg.PlotWidget.__init__(self, parent, **kwargs)

        self.getAxis("bottom").setLabel("Score")
        self.getAxis("left").setLabel("Counts")

        self.__data = None
        self.__histcurve = None

        self.__mode = Histogram.NoSelection
        self.__min = 0
        self.__max = 0

        def makeline(pos):
            pen = QtGui.QPen(Qt.darkGray, 1)
            pen.setCosmetic(True)
            line = InfiniteLine(angle=90, pos=pos, pen=pen, movable=True)
            line.setCursor(Qt.SizeHorCursor)
            return line

        self.__cuthigh = makeline(self.__max)
        self.__cuthigh.sigPositionChanged.connect(self.__on_cuthigh_changed)
        self.__cuthigh.sigPositionChangeFinished.connect(self.selectionEdited)
        self.__cutlow = makeline(self.__min)
        self.__cutlow.sigPositionChanged.connect(self.__on_cutlow_changed)
        self.__cutlow.sigPositionChangeFinished.connect(self.selectionEdited)

        brush = pg.mkBrush((200, 200, 200, 180))
        self.__taillow = pg.PlotCurveItem(
            fillLevel=0, brush=brush, pen=QtGui.QPen(Qt.NoPen))
        self.__taillow.setVisible(False)

        self.__tailhigh = pg.PlotCurveItem(
            fillLevel=0, brush=brush, pen=QtGui.QPen(Qt.NoPen))
        self.__tailhigh.setVisible(False)

    def setData(self, hist, bins=None):
        """
        Set the histogram data
        """
        if bins is None:
            bins = np.arange(len(hist))

        self.__data = (hist, bins)
        if self.__histcurve is None:
            self.__histcurve = pg.PlotCurveItem(
                x=bins, y=hist, stepMode=True
            )
        else:
            self.__histcurve.setData(x=bins, y=hist, stepMode=True)

        self.__update()

    def setHistogramCurve(self, curveitem):
        """
        Set the histogram plot curve.
        """
        if self.__histcurve is curveitem:
            return

        if self.__histcurve is not None:
            self.removeItem(self.__histcurve)
            self.__histcurve = None
            self.__data = None

        if curveitem is not None:
            if not curveitem.opts["stepMode"]:
                raise ValueError("The curve must have `stepMode == True`")
            self.addItem(curveitem)
            self.__histcurve = curveitem
            self.__data = (curveitem.yData, curveitem.xData)

        self.__update()

    def histogramCurve(self):
        """
        Return the histogram plot curve.
        """
        return self.__histcurve

    def setSelectionMode(self, mode):
        """
        Set selection mode
        """
        if self.__mode != mode:
            self.__mode = mode
            self.__update_cutlines()
            self.__update_tails()

    def setLower(self, value):
        """
        Set the lower boundary value.
        """
        if self.__min != value:
            self.__min = value
            self.__update_cutlines()
            self.__update_tails()
            self.selectionChanged.emit()

    def setUpper(self, value):
        """
        Set the upper boundary value.
        """
        if self.__max != value:
            self.__max = value
            self.__update_cutlines()
            self.__update_tails()
            self.selectionChanged.emit()

    def setBoundary(self, lower, upper):
        """
        Set lower and upper boundary value.
        """
        changed = False
        if self.__min != lower:
            self.__min = lower
            changed = True

        if self.__max != upper:
            self.__max = upper
            changed = True

        if changed:
            self.__update_cutlines()
            self.__update_tails()
            self.selectionChanged.emit()

    def boundary(self):
        """
        Return the lower and upper boundary values.
        """
        return (self.__min, self.__max)

    def clear(self):
        """
        Clear the plot.
        """
        self.__data = None
        self.__histcurve = None
        super().clear()

    def __update(self):
        def additem(item):
            if item.scene() is not self.scene():
                self.addItem(item)

        def removeitem(item):
            if item.scene() is self.scene():
                self.removeItem(item)

        if self.__data is not None:
            additem(self.__cuthigh)
            additem(self.__cutlow)
            additem(self.__tailhigh)
            additem(self.__taillow)

            _, edges = self.__data
            # Update the allowable cutoff line bounds
            minx, maxx = np.min(edges), np.max(edges)
            span = maxx - minx
            bounds = minx - span * 0.005, maxx + span * 0.005

            self.__cuthigh.setBounds(bounds)
            self.__cutlow.setBounds(bounds)

            self.__update_cutlines()
            self.__update_tails()
        else:
            removeitem(self.__cuthigh)
            removeitem(self.__cutlow)
            removeitem(self.__tailhigh)
            removeitem(self.__taillow)

    def __update_cutlines(self):
        self.__cuthigh.setVisible(self.__mode & Histogram.High)
        self.__cuthigh.setValue(self.__max)
        self.__cutlow.setVisible(self.__mode & Histogram.Low)
        self.__cutlow.setValue(self.__min)

    def __update_tails(self):
        if self.__mode == Histogram.NoSelection:
            return
        if self.__data is None:
            return

        hist, edges = self.__data

        self.__taillow.setVisible(self.__mode & Histogram.Low)
        if self.__min > edges[0]:
            datalow = histogram_cut(hist, edges, edges[0], self.__min)
            self.__taillow.setData(*datalow, fillLevel=0, stepMode=True)
        else:
            self.__taillow.clear()

        self.__tailhigh.setVisible(self.__mode & Histogram.High)
        if self.__max < edges[-1]:
            datahigh = histogram_cut(hist, edges, self.__max, edges[-1])
            self.__tailhigh.setData(*datahigh, fillLevel=0, stepMode=True)
        else:
            self.__tailhigh.clear()

    def __on_cuthigh_changed(self):
        self.setUpper(self.__cuthigh.value())

    def __on_cutlow_changed(self):
        self.setLower(self.__cutlow.value())


def histogram_cut(hist, bins, low, high):
    """
    Return a subset of a histogram between low and high values.

    Parameters
    ----------
    hist : (N, ) array
        The histogram values/counts for each bin.
    bins : (N + 1) array
        The histogram bin edges.
    low, high : float
        The lower and upper edge where to cut the histogram

    Returns
    -------
    histsubset : (M, ) array
        The histogram subset
    binssubset : (M + 1) array
        New histogram bins. The first and the last value are equal
        to `low` and `high` respectively.

    Note that in general the first and the final bin widths are
    different then the widths in the input bins

    """
    if len(bins) < 2:
        raise ValueError()

    if low >= high:
        raise ValueError()

    low = max(bins[0], low)
    high = min(bins[-1], high)

    if low <= bins[0]:
        lowidx = 0
    else:
        lowidx = np.searchsorted(bins, low, side="left")

    if high >= bins[-1]:
        highidx = len(bins)
    else:
        highidx = np.searchsorted(bins, high, side="right")

    cbins = bins[lowidx: highidx]
    chist = hist[lowidx: highidx - 1]

    if cbins[0] > low:
        cbins = np.r_[low, cbins]
        chist = np.r_[hist[lowidx - 1], chist]

    if cbins[-1] < high:
        cbins = np.r_[cbins, high]
        chist = np.r_[chist, hist[highidx - 1]]

    assert cbins.size == chist.size + 1
    return cbins, chist


def test_low(array, low, high):
    return array <= low


def test_high(array, low, high):
    return array >= high


def test_two_tail(array, low, high):
    return (array >= high) | (array <= low)


def test_middle(array, low, high):
    return (array <= high) | (array >= low)


class OWFeatureSelection(widget.OWWidget):
    name = "Differential Expression"
    description = "Gene selection by differential expression analysis."
    icon = "../widgets/icons/GeneSelection.svg"
    priority = 1010

    inputs = [("Data", Orange.data.Table, "set_data")]
    outputs = [("Data subset", Orange.data.Table, widget.Default),
               ("Remaining data subset", Orange.data.Table),
               ("Selected genes", Orange.data.Table)]

    #: Selection types
    LowTail, HighTail, TwoTail = 1, 2, 3
    #: Test type - i.e a two sample (t-test, ...) or multi-sample (ANOVA) test
    TwoSampleTest, VarSampleTest = 1, 2
    #: Available scoring methods

    Scores = [
        ("Fold Change", TwoTail, TwoSampleTest, score_fold_change),
        ("log2(Fold Change)", TwoTail, TwoSampleTest, score_log_fold_change),
        ("T-test", TwoTail, TwoSampleTest, score_ttest_t),
        ("T-test P-value", LowTail, TwoSampleTest, score_ttest_p),
        ("ANOVA", HighTail, VarSampleTest, score_anova_f),
        ("ANOVA P-value", LowTail, VarSampleTest, score_anova_p),
        ("Signal to Noise Ratio", TwoTail, TwoSampleTest,
         score_signal_to_noise),
        ("Mann-Whitney", LowTail, TwoSampleTest, score_mann_whitney_u),
    ]

    settingsHandler = SetContextHandler()

    #: Selected score index.
    score_index = settings.Setting(0)
    #: Compute the null score distribution (label permutations).
    compute_null = settings.Setting(False)
    #: Number of permutations to for null score distribution.
    permutations_count = settings.Setting(20)
    #: Alpha value (significance) for the selection on background
    #: null score distribution.
    alpha_value = settings.Setting(0.01)
    #: N best for the fixed best N scores selection.
    n_best = settings.Setting(20)

    #: Stored thresholds for scores.
    thresholds = settings.Setting({
        "Fold Change": (0.5, 2.),
        "log2(Fold Change)": (-1, 1),
        "T-test": (-2, 2),
        "T-test P-value": (0.01, 0.01),
        "ANOVA": (0, 3),
        "ANOVA P-value": (0, 0.01),
    })

    add_scores_to_output = settings.Setting(False)
    auto_commit = settings.Setting(False)

    #: Current target group index
    current_group_index = settings.ContextSetting(-1)
    #: Stored (persistent) values selection for all target split groups.
    stored_selections = settings.ContextSetting([])

    def __init__(self, parent=None):
        widget.OWWidget.__init__(self, parent)

        self.min_value, self.max_value = \
            self.thresholds.get(self.Scores[self.score_index][0], (1, 0))

        #: Input data set
        self.data = None
        #: Current target group selection
        self.targets = []
        #: The computed scores
        self.scores = None
        #: The computed scores from label permutations
        self.nulldist = None

        self.__scores_future = self.__scores_state = None

        self.__in_progress = False

        self.test_f = {
            OWFeatureSelection.LowTail: test_low,
            OWFeatureSelection.HighTail: test_high,
            OWFeatureSelection.TwoTail: test_two_tail,
        }

        self.histogram = Histogram(
            enableMouse=False, enableMenu=False, background="w"
        )
        self.histogram.enableAutoRange(enable=True)
        self.histogram.getViewBox().setMouseEnabled(False, False)
        self.histogram.selectionChanged.connect(
            self.__on_histogram_plot_selection_changed
        )
        self.histogram.selectionEdited.connect(
            self._invalidate_selection
        )

        self.mainArea.layout().addWidget(self.histogram)

        box = gui.widgetBox(self.controlArea, "Info")

        self.dataInfoLabel = gui.widgetLabel(box, "No data on input.\n")
        self.dataInfoLabel.setWordWrap(True)
        self.selectedInfoLabel = gui.widgetLabel(box, "\n")

        box1 = gui.widgetBox(self.controlArea, "Scoring Method")
        gui.comboBox(box1, self, "score_index",
                     items=[sm[0] for sm in self.Scores],
                     callback=[self.on_scoring_method_changed,
                               self.update_scores])

        box = gui.widgetBox(self.controlArea, "Target Labels")
        self.label_selection_widget = guiutils.LabelSelectionWidget(self)
        self.label_selection_widget.setMaximumHeight(150)
        box.layout().addWidget(self.label_selection_widget)

        self.label_selection_widget.groupChanged.connect(
            self.on_label_activated)

        self.label_selection_widget.groupSelectionChanged.connect(
            self.on_target_changed)

        box = gui.widgetBox(self.controlArea, "Selection")
        box.layout().setSpacing(0)

        self.max_value_spin = gui.doubleSpin(
            box, self, "max_value", minv=-1e6, maxv=1e6, step=1e-6,
            label="Upper threshold:", callback=self.update_boundary,
            callbackOnReturn=True)

        self.low_value_spin = gui.doubleSpin(
            box, self, "min_value", minv=-1e6, maxv=1e6, step=1e-6,
            label="Lower threshold:", callback=self.update_boundary,
            callbackOnReturn=True)

        check = gui.checkBox(
            box, self, "compute_null", "Compute null distribution",
            callback=self.update_scores)

        perm_spin = gui.spin(
            box, self, "permutations_count", minv=1, maxv=50,
            label="Permutations:", callback=self.update_scores,
            callbackOnReturn=True)

        check.disables.append(perm_spin)

        box1 = gui.widgetBox(box, orientation='horizontal')

        pval_spin = gui.doubleSpin(
            box1, self, "alpha_value", minv=2e-7, maxv=1.0, step=1e-7,
            label="α-value:")
        pval_select = gui.button(
            box1, self, "Select", callback=self.select_p_best,
            autoDefault=False
        )
        check.disables.append(pval_spin)
        check.disables.append(pval_select)

        check.makeConsistent()

        box1 = gui.widgetBox(box, orientation='horizontal')
        gui.spin(box1, self, "n_best", 0, 10000, step=1,
                 label="Best Ranked:")
        gui.button(box1, self, "Select", callback=self.select_n_best,
                   autoDefault=False)

        box = gui.widgetBox(self.controlArea, "Output")

        acbox = gui.auto_commit(
            box, self, "auto_commit", "Commit", box=None)
        acbox.button.setDefault(True)

        gui.checkBox(box, self, "add_scores_to_output",
                     "Add gene scores to output",
                     callback=self._invalidate_selection)

        gui.rubber(self.controlArea)

        self.on_scoring_method_changed()
        self._executor = concurrent.ThreadExecutor()

    def sizeHint(self):
        return QtCore.QSize(800, 600)

    def clear(self):
        """Clear the widget state.
        """
        self.data = None
        self.targets = []
        self.stored_selections = []
        self.nulldist = None
        self.scores = None
        self.label_selection_widget.clear()
        self.clear_plot()
        self.dataInfoLabel.setText("No data on input.\n")
        self.selectedInfoLabel.setText("\n")
        self.__cancel_pending()

    def clear_plot(self):
        """Clear the histogram plot.
        """
        self.histogram.clear()

    def initialize(self, data):
        """Initialize widget state from the data."""
        col_targets, row_targets = grouputils.group_candidates(data)
        modelitems = [guiutils.standarditem_from(obj)
                      for obj in col_targets + row_targets]

        model = QtGui.QStandardItemModel()
        for item in modelitems:
            model.appendRow(item)

        self.label_selection_widget.setModel(model)

        self.targets = col_targets + row_targets
        # Default selections for all group keys
        # (the first value is selected)
        self.stored_selections = [[0] for _ in self.targets]

    def selected_split(self):
        index = self.label_selection_widget.currentGroupIndex()
        if not (0 <= index < len(self.targets)):
            return None, ()

        grp = self.targets[index]
        selection = self.label_selection_widget.currentGroupSelection()
        selected_indices = [ind.row() for ind in selection.indexes()]
        return grp, selected_indices

    def set_data(self, data):
        self.closeContext()

        self.clear()
        self.error([0, 1])
        self.data = data

        if self.data is not None:
            self.initialize(data)

        if self.data is not None and not self.targets:
            # If both attr. labels and classes are missing, show an error
            self.error(
                1, "Cannot compute gene scores! Differential expression "
                   "widget requires a data-set with a discrete class "
                   "variable(s) or column labels!"
            )
            self.data = None

        if self.data is not None:
            # Initialize the selected groups/labels.
            # Default selected group key
            index = 0
            rowshint = data_hints.get_hint(data, "genesinrows", False)
            if not rowshint:
                # Select the first row group split candidate (if available)
                indices = [i for i, grp in enumerate(self.targets)
                           if isinstance(grp, grouputils.RowGroup)]
                if indices:
                    index = indices[0]

            self.current_group_index = index

            # Restore target label selection from context settings
            items = {(grp.name, val)
                     for grp in self.targets for val in grp.values}
            self.openContext(items)
            # Restore current group / selection
            model = self.label_selection_widget.model()
            selection = [model.index(i, 0, model.index(keyind, 0))
                         for keyind, selection in enumerate(self.stored_selections)
                         for i in selection]
            selection = guiutils.itemselection(selection)

            self.label_selection_widget.setSelection(selection)
            self.label_selection_widget.setCurrentGroupIndex(
                self.current_group_index)

        self.commit()

    def update_scores(self):
        """Compute the scores and update the histogram.
        """
        self.__cancel_pending()
        self.clear_plot()
        self.scores = None
        self.nulldist = None
        self.error(0)

        grp, split_selection = self.selected_split()

        if not self.data or grp is None:
            return

        _, side, test_type, score_func = self.Scores[self.score_index]

        def compute_scores(X, group_indices, warn=False):
            arrays = [X[ind] for ind in group_indices]
            ss = score_func(*arrays, axis=0)
            return ss[0] if isinstance(ss, tuple) and not warn else ss

        def permute_indices(group_indices, random_state=None):
            assert all(ind.dtype.kind == "i" for ind in group_indices)
            assert all(ind.ndim == 1 for ind in group_indices)
            if random_state is None:
                random_state = np.random
            joined = np.hstack(group_indices)
            random_state.shuffle(joined)
            split_ind = np.cumsum([len(ind) for ind in group_indices])
            return np.split(joined, split_ind[:-1])

        if isinstance(grp, grouputils.RowGroup):
            axis = 0
        else:
            axis = 1

        if test_type == OWFeatureSelection.TwoSampleTest:
            G1 = grouputils.group_selection_mask(
                self.data, grp, split_selection)
            G2 = ~G1
            indices = [np.flatnonzero(G1), np.flatnonzero(G2)]
        elif test_type == self.VarSampleTest:
            indices = [grouputils.group_selection_mask(self.data, grp, [i])
                       for i in range(len(grp.values))]
            indices = [np.flatnonzero(ind) for ind in indices]
        else:
            assert False

        if not all(np.count_nonzero(ind) > 0 for ind in indices):
            self.error(0, "Target labels most exclude/include at least one "
                          "value.")
            self.scores = None
            self.nulldist = None
            self.update_data_info_label()
            return

        X = self.data.X
        if axis == 1:
            X = X.T

        # TODO: Check that each label has more than one measurement,
        # raise warning otherwise.

        def compute_scores_with_perm(X, indices, nperm=0, rstate=None,
                                     progress_advance=None):
            warning = None
            scores = compute_scores(X, indices, warn=True)
            if isinstance(scores, tuple):
                scores, warning = scores

            if progress_advance is not None:
                progress_advance()
            null_scores = []
            if nperm > 0:
                if rstate is None:
                    rstate = np.random.RandomState(0)

                for i in range(nperm):
                    p_indices = permute_indices(indices, rstate)
                    assert all(pind.shape == ind.shape
                               for pind, ind in zip(indices, p_indices))
                    pscore = compute_scores(X, p_indices)
                    assert pscore.shape == scores.shape
                    null_scores.append(pscore)
                    if progress_advance is not None:
                        progress_advance()

            return scores, null_scores, warning

        p_advance = concurrent.methodinvoke(
            self, "progressBarAdvance", (float,))
        state = namespace(cancelled=False, advance=p_advance)

        def progress():
            if state.cancelled:
                raise concurrent.CancelledError
            else:
                state.advance(100 / (nperm + 1))

        self.progressBarInit()
        set_scores = concurrent.methodinvoke(
            self, "__set_score_results", (concurrent.Future,))

        nperm = self.permutations_count if self.compute_null else 0
        self.__scores_state = state
        self.__scores_future = self._executor.submit(
                compute_scores_with_perm, X, indices, nperm,
                progress_advance=progress)
        self.__scores_future.add_done_callback(set_scores)

    @Slot(float)
    def __pb_advance(self, value):
        self.progressBarAdvance(value, )

    @Slot(float)
    def progressBarAdvance(self, value):
        if not self.__in_progress:
            self.__in_progress = True
            try:
                super().progressBarAdvance(value)
            finally:
                self.__in_progress = False

    @Slot(concurrent.Future)
    def __set_score_results(self, scores):
        # set score results from a Future
        self.error(1)
        self.warning(10)
        if scores is self.__scores_future:
            self.histogram.setUpdatesEnabled(True)
            self.progressBarFinished()

            if not self.__scores_state.cancelled:
                try:
                    results = scores.result()
                except Exception as ex:
                    sys.excepthook(*sys.exc_info())
                    self.error(1, "Error: {!s}", ex)
                else:
                    self.set_scores(*results)

        elif self.__scores_future is None:
            self.histogram.setUpdatesEnabled(True)
            self.progressBarFinished()

    def __cancel_pending(self):
        if self.__scores_future is not None:
            self.__scores_future.cancel()
            self.__scores_state.cancelled = True
            self.__scores_state = self.__scores_future = None

    def set_scores(self, scores, null_scores=None, warning=None):
        self.scores = scores
        self.nulldist = null_scores

        if null_scores:
            nulldist = np.array(null_scores, dtype=float)
        else:
            nulldist = None

        self.warning(10, warning)

        self.setup_plot(self.score_index, scores, nulldist)
        self.update_data_info_label()
        self.update_selected_info_label()
        self.commit()

    def setup_plot(self, scoreindex, scores, nulldist=None):
        """
        Setup the score histogram plot

        Parameters
        ----------
        scoreindex : int
            Score index (into OWFeatureSelection.Scores)
        scores : (N, ) array
            The scores obtained
        nulldist (P, N) array optional
            The scores obtained under P permutations of labels.
        """
        score_name, side, test_type, _ = self.Scores[scoreindex]
        low, high = self.thresholds.get(score_name, (-np.inf, np.inf))

        validmask = np.isfinite(scores)
        validscores = scores[validmask]

        nbins = int(max(np.ceil(np.sqrt(len(validscores))), 20))
        freq, edges = np.histogram(validscores, bins=nbins)
        self.histogram.setHistogramCurve(
            pg.PlotCurveItem(x=edges, y=freq, stepMode=True,
                             pen=pg.mkPen("b", width=2))
        )

        if nulldist is not None:
            nulldist = nulldist.ravel()
            validmask = np.isfinite(nulldist)
            validnulldist = nulldist[validmask]
            nullbins = edges  # XXX: extend to the full range of nulldist
            nullfreq, _ = np.histogram(validnulldist, bins=nullbins)
            nullfreq = nullfreq * (freq.sum() / nullfreq.sum())
            nullitem = pg.PlotCurveItem(
                x=nullbins, y=nullfreq, stepMode=True,
                pen=pg.mkPen((50, 50, 50, 100))
            )
            # Ensure it stacks behind the main curve
            nullitem.setZValue(nullitem.zValue() - 10)
            self.histogram.addItem(nullitem)

        # Restore saved thresholds
        eps = np.finfo(float).eps
        minx, maxx = edges[0] - eps, edges[-1] + eps

        low, high = max(low, minx), min(high, maxx)

        if side == OWFeatureSelection.LowTail:
            mode = Histogram.Low
        elif side == OWFeatureSelection.HighTail:
            mode = Histogram.High
        elif side == OWFeatureSelection.TwoTail:
            mode = Histogram.TwoSided
        else:
            assert False
        self.histogram.setSelectionMode(mode)
        self.histogram.setBoundary(low, high)

        # If this is a two sample test add markers to the left and right
        # plot indicating which group is over-expressed in that part
        if test_type == OWFeatureSelection.TwoSampleTest and \
                side == OWFeatureSelection.TwoTail:
            maxy = np.max(freq)
            # XXX: Change use of integer constant
            if scoreindex == 0:  # fold change is centered on 1.0
                x1, y1 = (minx + 1) / 2, maxy
                x2, y2 = (maxx + 1) / 2, maxy
            else:
                x1, y1 = minx / 2, maxy
                x2, y2 = maxx / 2, maxy

            grp, selected_indices = self.selected_split()

            values = grp.values
            selected_values = [values[i] for i in selected_indices]

            left = ", ".join(v for v in values if v not in selected_values)
            right = ", ".join(v for v in selected_values)

            labelitem = pg.TextItem(left, color=(40, 40, 40))
            labelitem.setPos(x1, y1)
            self.histogram.addItem(labelitem)

            labelitem = pg.TextItem(right, color=(40, 40, 40))
            labelitem.setPos(x2, y2)
            self.histogram.addItem(labelitem)

    def update_data_info_label(self):
        if self.data is not None:
            samples, genes = len(self.data), len(self.data.domain.attributes)
            grp, indices = self.selected_split()
            if isinstance(grp, grouputils.ColumnGroup):
                samples, genes = genes, samples

            target_labels = [grp.values[i] for i in indices]
            text = "%i samples, %i genes\n" % (samples, genes)
            text += "Sample target: '%s'" % (",".join(target_labels))
        else:
            text = "No data on input.\n"

        self.dataInfoLabel.setText(text)

    def update_selected_info_label(self):
        pl = lambda c: "" if c == 1 else "s"
        if self.data is not None and self.scores is not None:
            scores = self.scores
            low, high = self.min_value, self.max_value
            _, side, _, _ = self.Scores[self.score_index]
            test = self.test_f[side]
            count_undef = np.count_nonzero(np.isnan(scores))
            count_scores = len(scores)
            scores = scores[np.isfinite(scores)]

            nselected = np.count_nonzero(test(scores, low, high))
            defined_txt = ("{} of {} score{} undefined."
                           .format(count_undef, count_scores, pl(count_scores)))

        elif self.data is not None:
            nselected = 0
            defined_txt = "No defined scores"
        else:
            nselected = 0
            defined_txt = ""

        self.selectedInfoLabel.setText(
            defined_txt + "\n" +
            "{} selected gene{}".format(nselected, pl(nselected))
        )

    def __on_histogram_plot_selection_changed(self):
        low, high = self.histogram.boundary()
        scorename, side, _, _ = self.Scores[self.score_index]
        self.thresholds[scorename] = (low, high)
        self.min_value = low
        self.max_value = high
        self.update_selected_info_label()

    def update_boundary(self):
        # The cutoff boundary value has been changed by the user
        # (in the controlArea widgets). Update the histogram plot
        # accordingly.
        if self.data is None:
            return

        _, side, _, _ = self.Scores[self.score_index]
        if side == OWFeatureSelection.LowTail:
            self.histogram.setLower(self.min_value)
        elif side == OWFeatureSelection.HighTail:
            self.histogram.setUpper(self.max_value)
        elif side == OWFeatureSelection.TwoTail:
            self.histogram.setBoundary(self.min_value, self.max_value)

        self._invalidate_selection()

    def select_n_best(self):
        """
        Select the `self.n_best` scored genes.
        """
        if self.scores is None:
            return

        score_name, side, _, _ = self.Scores[self.score_index]
        scores = self.scores
        scores = np.sort(scores[np.isfinite(scores)])

        if side == OWFeatureSelection.HighTail:
            cut = scores[-np.clip(self.n_best, 1, len(scores))]
            self.histogram.setUpper(cut)
        elif side == OWFeatureSelection.LowTail:
            cut = scores[np.clip(self.n_best, 0, len(scores) - 1)]
            self.histogram.setLower(cut)
        elif side == OWFeatureSelection.TwoTail:
            n = min(self.n_best, len(scores))
            scoresabs = np.abs(scores)
            if score_name == "Fold Change":
                # comparing fold change on a logarithmic scale
                scores = np.log2(scoresabs)
                scores = scores[np.isfinite(scoresabs)]
            scoresabs = np.sort(np.abs(scores))
            limit = (scoresabs[-n] + scoresabs[-min(n+1, len(scores))]) / 2
            cuthigh, cutlow = limit, -limit
            if score_name == "Fold Change":
                cuthigh, cutlow = 2**cuthigh, 2**cutlow
            self.histogram.setBoundary(cutlow, cuthigh)
        self._invalidate_selection()

    def select_p_best(self):
        if not self.nulldist:
            return

        _, side, _, _ = self.Scores[self.score_index]
        nulldist = np.asarray(self.nulldist).ravel()
        nulldist = nulldist[np.isfinite(nulldist)]
        nulldist = np.sort(nulldist)

        assert 0 <= self.alpha_value <= 1
        p = self.alpha_value
        if side == OWFeatureSelection.HighTail:
            cut = np.percentile(nulldist, [100 * (1 - p)])
            self.max_value = cut
            self.histogram.setUpper(cut)
        elif side == OWFeatureSelection.LowTail:
            cut = np.percentile(nulldist, [100 * p])
            self.min_value = cut
            self.histogram.setLower(cut)
        elif side == OWFeatureSelection.TwoTail:
            p1, p2 = np.percentile(nulldist, [100 * p / 2, 100 * (1 - p / 2)])
            self.histogram.setBoundary(p1, p2)
        self._invalidate_selection()

    def _invalidate_selection(self):
        self.commit()

    def on_target_changed(self):
        grp, indices = self.selected_split()
        if grp is None:
            return
        # Store target group label selection.
        self.stored_selections[self.targets.index(grp)] = indices
        self.update_scores()

    def on_label_activated(self, index):
        self.current_group_index = index
        self.update_scores()

    def on_scoring_method_changed(self):
        _, _, test_type, _ = self.Scores[self.score_index]
        self.label_selection_widget.values_view.setEnabled(
            test_type == OWFeatureSelection.TwoSampleTest
        )
        self.__update_threshold_spinbox()

    def __update_threshold_spinbox(self):
        _, side, _, _ = self.Scores[self.score_index]
        self.low_value_spin.setVisible(side & OWFeatureSelection.LowTail)
        self.max_value_spin.setVisible(side & OWFeatureSelection.HighTail)

    def commit(self):
        """
        Commit (send) the outputs.
        """
        if self.data is None or self.scores is None:
            return

        grp, _ = self.selected_split()
        if isinstance(grp, grouputils.RowGroup):
            axis = 1
        else:
            axis = 0

        score_name, side, _, _ = self.Scores[self.score_index]
        low, high = self.histogram.boundary()

        scores = self.scores
        mask = np.isfinite(scores)
        test = self.test_f[side]
        selected_masked = test(scores[mask], low, high)
        selected = np.zeros_like(scores, dtype=bool)
        selected[mask] = selected_masked

        indices = np.flatnonzero(selected)
        remaining = np.flatnonzero(~selected)

        domain = self.data.domain

        if axis == 0:
            # Select rows
            score_var = Orange.data.ContinuousVariable(score_name)
            domain = Orange.data.Domain(domain.attributes, domain.class_vars,
                                        domain.metas + (score_var,))
            data = self.data.from_table(domain, self.data)
            data[:, score_var] = np.c_[scores]
            subsetdata = data[indices]
            remainingdata = data[remaining]
        else:
            # select columns
            attrs = [copy_variable(var) for var in domain.attributes]
            for var, score in zip(attrs, scores):
                var.attributes[score_name] = str(score)

            selected_attrs = [attrs[i] for i in indices]
            remaining_attrs = [attrs[i] for i in remaining]

            domain = Orange.data.Domain(
                selected_attrs, domain.class_vars, domain.metas)
            subsetdata = self.data.from_table(domain, self.data)

            domain = Orange.data.Domain(
                remaining_attrs, domain.class_vars, domain.metas)
            remainingdata = self.data.from_table(domain, self.data)

        self.send("Data subset", subsetdata)
        self.send("Remaining data subset", remainingdata)
        self.send("Selected genes", None)

    def onDeleteWidget(self):
        super().onDeleteWidget()
        self.clear()
        self.__cancel_pending()
        self._executor.shutdown(wait=True)


def copy_variable(var):
    clone = var.copy(compute_value=transformation.Identity(var))
    clone.attributes = dict(var.attributes)
    return clone

import unittest


class Test_f_oneway(unittest.TestCase):
    def test_f_oneway(self):
        g1 = np.array([0.1, -0.1, 0.2, -0.2])
        g2 = g1 + 1
        g3 = g1

        f1, p1 = scipy.stats.f_oneway(g1, g2)
        f, p = f_oneway(g1, g2)
        np.testing.assert_almost_equal([f, p], [f1, p1])

        f, p = f_oneway(np.c_[g1], np.c_[g2], axis=0)
        np.testing.assert_almost_equal([f[0], p[0]], [f1, p1])

        f1, p1 = scipy.stats.f_oneway(g1, g2, g3)
        f, p = f_oneway(g1, g2, g3)
        np.testing.assert_almost_equal([f, p], [f1, p1])

        G1 = np.random.normal(size=(10, 30))
        G2 = np.random.normal(loc=1, size=(10, 20))
        G3 = np.random.normal(loc=2, size=(10, 10))

        F, P = f_oneway(G1, G2, G3, axis=1)
        self.assertEqual(F.shape, (10,))
        self.assertEqual(P.shape, (10,))

        FP1 = [scipy.stats.f_oneway(g1, g2, g3)
               for g1, g2, g3 in zip(G1, G2, G3)]

        F1 = [f for f, _ in FP1]
        P1 = [p for _, p in FP1]
        np.testing.assert_almost_equal(F1, F)
        np.testing.assert_almost_equal(P1, P)

        F, P = f_oneway(G1.T, G2.T, G3.T, axis=0)
        np.testing.assert_almost_equal(F1, F)
        np.testing.assert_almost_equal(P1, P)


def test_main(argv=sys.argv):
    app = QtGui.QApplication(argv)
    if len(argv) > 1:
        filename = argv[1]
    else:
        filename = "brown-selected"
    data = Orange.data.Table(filename)

    w = OWFeatureSelection()
    w.show()
    w.raise_()
    w.set_data(data)
    rval = app.exec_()
    w.set_data(None)
    w.saveSettings()
    w.onDeleteWidget()
    return rval

if __name__ == "__main__":
    sys.exit(test_main())
