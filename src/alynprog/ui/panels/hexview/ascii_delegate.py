"""A table delegate that highlights, in the ASCII column, the character(s) of the selected byte.

The ASCII column renders a whole row's 16 bytes as one string, so highlighting the character that
corresponds to the selected hex cell can't be done by cell selection alone — this delegate paints
the relevant sub-range of the string with the palette's highlight colours. Other columns fall
through to the default rendering.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QFontMetrics, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

from alynprog.ui.panels.hexview.model import HexTableModel


class AsciiHighlightDelegate(QStyledItemDelegate):
    def __init__(self, model: HexTableModel, parent=None) -> None:
        super().__init__(parent)
        self._model = model

    def paint(self, painter, option, index) -> None:
        if index.column() != self._model.ascii_column():
            super().paint(painter, option, index)
            return

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""  # we draw the text ourselves
        widget = opt.widget
        style = widget.style() if widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)

        rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, widget)
        fm = QFontMetrics(opt.font)
        align = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft

        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        base_role = QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text

        painter.save()
        painter.setFont(opt.font)
        painter.setPen(opt.palette.color(base_role))
        painter.drawText(rect, align, text)

        hl_row, lo, hi = self._model.ascii_highlight()
        lo = max(0, min(lo, len(text)))
        hi = max(0, min(hi, len(text)))
        if index.row() == hl_row and hi > lo:
            # Measure the actual prefix widths so the highlight lands on the right glyphs even if
            # the resolved font is not perfectly fixed-width (no accumulating per-char drift).
            x_lo = rect.left() + fm.horizontalAdvance(text[:lo])
            x_hi = rect.left() + fm.horizontalAdvance(text[:hi])
            seg_rect = QRect(x_lo, rect.top(), x_hi - x_lo, rect.height())
            painter.fillRect(seg_rect, opt.palette.highlight())
            painter.setPen(opt.palette.color(QPalette.ColorRole.HighlightedText))
            painter.drawText(seg_rect, align, text[lo:hi])
        painter.restore()
