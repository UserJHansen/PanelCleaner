import PySide6.QtWidgets as Qw
import PySide6.QtGui as Qg
import PySide6.QtCore as Qc
from logzero import logger


class CElidedLabel(Qw.QFrame):
    elisionChanged = Qc.Signal(bool)

    def __init__(self, text, parent=None):
        super(CElidedLabel, self).__init__(parent)

        self._elided = False
        self._content = text
        self._elideMode = Qc.Qt.ElideRight
        self.setSizePolicy(Qw.QSizePolicy.Expanding, Qw.QSizePolicy.Preferred)
        self.setFrameStyle(Qw.QFrame.NoFrame)

    def text(self):
        return self._content

    def setText(self, newText):
        self._content = newText
        self.update()

    def isElided(self):
        return self._elided

    def elideMode(self):
        return self._elideMode

    def setElideMode(self, mode):
        if mode in [Qc.Qt.ElideLeft, Qc.Qt.ElideMiddle, Qc.Qt.ElideRight, Qc.Qt.ElideNone]:
            self._elideMode = mode
            self.update()
        else:
            raise ValueError(f"Invalid elide mode {mode}")

    def paintEvent(self, event):
        painter = Qg.QPainter(self)
        fontMetrics = painter.fontMetrics()

        didElide = False
        lineSpacing = fontMetrics.lineSpacing()

        # For vertical centering, calculate the y-position
        y = (self.height() - lineSpacing) // 2 + fontMetrics.ascent()

        textLayout = Qg.QTextLayout(self._content, painter.font())
        textLayout.beginLayout()
        while True:
            line = textLayout.createLine()

            if not line.isValid():
                break

            line.setLineWidth(self.width())

            lastLine = self._content[line.textStart() :]
            elidedLastLine = fontMetrics.elidedText(lastLine, self._elideMode, self.width())
            painter.drawText(Qc.QPoint(0, y), elidedLastLine)
            line = textLayout.createLine()
            didElide = line.isValid()
            break

        textLayout.endLayout()

        if didElide != self._elided:
            self._elided = didElide
            self.elisionChanged.emit(didElide)
