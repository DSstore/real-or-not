"""The Scam Quiz tab of the dashboard: summary cards, an accuracy trend, the topics to practise, and a table of rounds."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QFrame, QGridLayout, QHeaderView, QLabel, QSplitter, QStackedWidget, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from app import dashboard_model as shared_model
from app import quiz_model as model

CARD_TITLES = ["Rounds", "Accuracy", "Best streak", "Answer time", "Level now", "Challenge"]


class QuizPanel(QWidget):
    """One player's scam quiz rounds. It only displays; the window reads the store and calls ``show_rounds``."""

    def __init__(self, username: str) -> None:
        super().__init__()
        self.cards: dict[str, QLabel] = {}
        cards = QGridLayout()
        for column, title in enumerate(CARD_TITLES):
            box = QFrame()
            box.setFrameShape(QFrame.Shape.StyledPanel)
            value, caption = QLabel(model.NO_VALUE), QLabel(title)
            value.setStyleSheet("font-size: 22px; font-weight: bold;")
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            inner = QVBoxLayout(box)
            inner.addWidget(value)
            inner.addWidget(caption)
            cards.addWidget(box, 0, column)
            self.cards[title] = value

        self.practice_label = QLabel("")
        self.practice_label.setWordWrap(True)
        self.practice_label.setAccessibleName("Topic to practise")

        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure
        self.figure = Figure(figsize=(7, 3.4), tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setMinimumHeight(240)
        self.table = QTableWidget(0, len(model.TABLE_HEADERS))
        self.table.setHorizontalHeaderLabels(model.TABLE_HEADERS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.canvas)
        splitter.addWidget(self.table)

        self.empty_label = QLabel(
            f"No quiz rounds yet for {username}.\nStart the result receiver with --user {username} "
            "(player 2 uses --user2), play the Scam Quiz, and the rounds will appear here.")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pages = QStackedWidget()
        self.pages.addWidget(splitter)
        self.pages.addWidget(self.empty_label)

        layout = QVBoxLayout(self)
        layout.addLayout(cards)
        layout.addWidget(self.practice_label)
        layout.addWidget(self.pages, 1)

    def show_rounds(self, documents: list[dict]) -> None:
        summary = model.summarize(documents)
        scores = model.category_scores(documents)
        self.cards["Rounds"].setText(str(summary.rounds))
        self.cards["Accuracy"].setText(shared_model.percent(summary.accuracy))
        self.cards["Best streak"].setText(str(summary.best_streak) if summary.rounds else model.NO_VALUE)
        self.cards["Answer time"].setText(shared_model.seconds(summary.average_answer_time))
        self.cards["Level now"].setText(model.NO_VALUE if summary.latest_level is None else str(summary.latest_level))
        self.cards["Challenge"].setText(
            model.NO_VALUE if summary.challenge_asked == 0
            else f"{summary.challenge_correct}/{summary.challenge_asked}")
        self.practice_label.setText(self.practice_text(scores))

        self.table.setRowCount(len(documents))
        for row, document in enumerate(documents):
            for column, text in enumerate(model.table_row(document)):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, column, item)
        self._draw(documents, scores)
        self.pages.setCurrentIndex(0 if documents else 1)

    @staticmethod
    def practice_text(scores: list[model.CategoryScore]) -> str:
        if not scores:
            return ""
        weak = model.weakest(scores)
        if weak is None:
            return "No weak topics yet: every topic asked at least twice was answered correctly."
        return (f"Topic to practise: <b>{weak.label}</b> - {weak.correct} of {weak.asked} answered correctly "
                f"({shared_model.percent(weak.accuracy)}).")

    def _draw(self, documents: list[dict], scores: list[model.CategoryScore]) -> None:
        """Left: accuracy by round, oldest first. Right: accuracy by topic, weakest at the top."""
        data = model.trend(documents)
        palette = self.palette()
        background, text = palette.window().color().name(), palette.windowText().color().name()
        self.figure.clear()
        self.figure.set_facecolor(background)
        left, right = self.figure.subplots(1, 2, gridspec_kw={"width_ratios": [3, 2]})
        for axis in (left, right):
            axis.set_facecolor(background)
            axis.tick_params(colors=text)
            axis.grid(True, alpha=0.3)
            for spine in axis.spines.values():
                spine.set_color(text)

        values = [float("nan") if v is None else v * 100 for v in data.accuracy]
        left.plot(range(1, len(values) + 1), values, marker="o", linewidth=1.5)
        left.set_ylim(0, 105)
        left.set_ylabel("Accuracy (%)", color=text)
        left.set_xlabel("Round (oldest to newest)", color=text)
        left.xaxis.get_major_locator().set_params(integer=True)

        shown = scores[:8]  # The weakest few; a long list would not fit.
        right.barh(range(len(shown)), [s.accuracy * 100 for s in shown])
        right.set_yticks(range(len(shown)))
        right.set_yticklabels([f"{s.label} ({s.correct}/{s.asked})" for s in shown], color=text, fontsize=8)
        right.invert_yaxis()  # Weakest first, at the top.
        right.set_xlim(0, 105)
        right.set_xlabel("Accuracy by topic (%)", color=text)
        right.grid(True, axis="x", alpha=0.3)
        right.grid(False, axis="y")
        self.canvas.draw_idle()
