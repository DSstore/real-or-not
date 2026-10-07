"""PyQt6 dashboard: log in, then see your own Reach Garden rounds, summary numbers, and a trend chart.

Run with:  .\.venv\Scripts\python.exe -m app.dashboard
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QApplication, QDialog, QFileDialog, QMessageBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QMainWindow, QPushButton, QSplitter, QStackedWidget,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from app import dashboard_model as model
from app.report import DEFAULT_REPORT_DIR, MAX_ROUNDS, ReportError, build_report, default_filename, write_pdf
from backend.auth import AuthError, User, UserStore
from backend.result_receiver import add_store_arguments, store_from_args
from backend.storage import ResultStore, StorageError
from backend.users import DEFAULT_USERS_DB, open_users
from shared.config import ConfigurationError, load_settings
from shared.logger import start_logging

LOGGER = logging.getLogger("motionplay.app.dashboard")

ROUND_LIMIT = 500  # The dashboard shows the most recent rounds only.
REFRESH_MS = 5000


class LoginDialog(QDialog):
    """Log in, or create an account and log in. ``user`` is set when it is accepted."""

    def __init__(self, users: UserStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._users = users
        self._creating = False
        self.user: User | None = None
        self.setWindowTitle("MotionPlay - Log in")
        self.setMinimumWidth(340)

        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("Username")
        self.username_edit.setMaxLength(32)
        self.password_edit = QLineEdit()
        self.password_edit.setPlaceholderText("Password")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.repeat_edit = QLineEdit()
        self.repeat_edit.setPlaceholderText("Repeat password")
        self.repeat_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.repeat_edit.setVisible(False)
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setAccessibleName("Login message")
        self.primary_button = QPushButton("Log in")
        self.primary_button.setDefault(True)
        self.mode_button = QPushButton("Create account")
        self.quit_button = QPushButton("Quit")

        buttons = QHBoxLayout()
        buttons.addWidget(self.mode_button)
        buttons.addStretch(1)
        buttons.addWidget(self.quit_button)
        buttons.addWidget(self.primary_button)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>MotionPlay</b>"))
        for widget in (self.username_edit, self.password_edit, self.repeat_edit, self.error_label):
            layout.addWidget(widget)
        layout.addLayout(buttons)

        self.primary_button.clicked.connect(self._submit)
        self.mode_button.clicked.connect(self._toggle_mode)
        self.quit_button.clicked.connect(self.reject)
        for edit in (self.username_edit, self.password_edit, self.repeat_edit):
            edit.returnPressed.connect(self._submit)

    def _toggle_mode(self) -> None:
        self._creating = not self._creating
        self.repeat_edit.setVisible(self._creating)
        self.repeat_edit.clear()
        self.primary_button.setText("Create account and log in" if self._creating else "Log in")
        self.mode_button.setText("Back to log in" if self._creating else "Create account")
        self.setWindowTitle("MotionPlay - Create account" if self._creating else "MotionPlay - Log in")
        self.error_label.setText("")

    def _submit(self) -> None:
        username, password = self.username_edit.text().strip(), self.password_edit.text()
        try:
            if self._creating:
                if self.repeat_edit.text() != password:
                    raise AuthError("The two passwords do not match.")
                self._users.create_user(username, password)
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)  # bcrypt takes a moment on purpose
            try:
                self.user = self._users.authenticate(username, password)
            finally:
                QApplication.restoreOverrideCursor()
        except AuthError as error:
            self.error_label.setText(f"Error: {error}")
            self.password_edit.clear()
            self.repeat_edit.clear()
            return
        except StorageError as error:
            self.error_label.setText(f"Error: {error}")
            return
        self.password_edit.clear()
        self.accept()


class DashboardWindow(QMainWindow):
    """One player's rounds. Reads only that player's rounds from the store."""

    CARD_TITLES = ["Rounds", "Accuracy", "Best streak", "Reaction", "Hold stability", "Path efficiency"]

    def __init__(self, store: ResultStore, user: User, refresh_ms: int = REFRESH_MS) -> None:
        super().__init__()
        self._store, self._user = store, user
        self.logged_out = False
        self.setWindowTitle(f"MotionPlay - {user.username}")
        self.resize(980, 720)

        self.refresh_button = QPushButton("Refresh")
        self.export_button = QPushButton("Export report...")
        self.logout_button = QPushButton("Log out")
        header = QHBoxLayout()
        header.addWidget(QLabel(f"<b>MotionPlay</b> &nbsp; Signed in as <b>{user.username}</b>"))
        header.addStretch(1)
        header.addWidget(self.refresh_button)
        header.addWidget(self.export_button)
        header.addWidget(self.logout_button)

        self.cards: dict[str, QLabel] = {}
        cards = QGridLayout()
        for column, title in enumerate(self.CARD_TITLES):
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

        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure
        self.figure = Figure(figsize=(6, 3.2), tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setMinimumHeight(220)
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
            f"No rounds yet for {user.username}.\nStart the result receiver with --user {user.username}, "
            "play Reach Garden, and the rounds will appear here.")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pages = QStackedWidget()
        self.pages.addWidget(splitter)
        self.pages.addWidget(self.empty_label)

        self.status_label = QLabel("")
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addLayout(header)
        layout.addLayout(cards)
        layout.addWidget(self.pages, 1)
        layout.addWidget(self.status_label)
        self.setCentralWidget(root)

        self.refresh_button.clicked.connect(self.refresh)
        self.export_button.clicked.connect(self._choose_and_export)
        self.logout_button.clicked.connect(self._log_out)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        if refresh_ms > 0:
            self.timer.start(refresh_ms)
        self.refresh()

    def _choose_and_export(self) -> None:
        now = datetime.now()
        DEFAULT_REPORT_DIR.mkdir(parents=True, exist_ok=True)
        suggestion = str(DEFAULT_REPORT_DIR / default_filename(self._user.username, now))
        chosen, _filter = QFileDialog.getSaveFileName(self, "Save progress report", suggestion, "PDF files (*.pdf)")
        if chosen:  # An empty string means the player cancelled.
            self.export_report(Path(chosen))

    def export_report(self, path: Path) -> bool:
        """Write this player's progress report as a PDF. Returns whether it worked; the status line says why not."""
        try:
            documents = self._store.list_sessions(game=model.GARDEN_GAME, user_id=self._user.user_id, limit=MAX_ROUNDS)
            pages = build_report(documents, self._user.username, datetime.now())
            write_pdf(pages, path)
        except (ReportError, StorageError) as error:
            LOGGER.warning("Report not saved for %s: %s", self._user.username, error)
            self.status_label.setText(f"Report not saved: {error}")
            return False
        LOGGER.info("Report saved for %s: %d page(s), %s", self._user.username, len(pages), path)
        self.status_label.setText(f"Report saved ({len(pages)} pages): {path}")
        return True

    def _log_out(self) -> None:
        self.logged_out = True
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt name)
        self.timer.stop()
        super().closeEvent(event)

    def refresh(self) -> None:
        try:
            documents = self._store.list_sessions(game=model.GARDEN_GAME, user_id=self._user.user_id, limit=ROUND_LIMIT)
        except StorageError as error:
            LOGGER.warning("Could not read rounds for %s: %s", self._user.username, error)
            self.status_label.setText(f"Could not read rounds: {error}")
            return
        summary = model.summarize(documents)
        self.cards["Rounds"].setText(str(summary.rounds))
        self.cards["Accuracy"].setText(model.percent(summary.accuracy))
        self.cards["Best streak"].setText(str(summary.best_streak) if summary.rounds else model.NO_VALUE)
        self.cards["Reaction"].setText(model.seconds(summary.average_reaction))
        self.cards["Hold stability"].setText(model.percent(summary.average_stability))
        self.cards["Path efficiency"].setText(model.percent(summary.average_efficiency))

        self.table.setRowCount(len(documents))
        for row, document in enumerate(documents):
            for column, text in enumerate(model.table_row(document)):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, column, item)
        self._draw_trend(documents)
        self.pages.setCurrentIndex(0 if documents else 1)
        shown = f"showing the {len(documents)} most recent" if len(documents) == ROUND_LIMIT else f"{len(documents)} round(s)"
        self.status_label.setText(f"Updated {datetime.now().strftime('%H:%M:%S')} - {shown}. "
                                  f"Total play time {model.duration(summary.total_seconds)}.")

    def _draw_trend(self, documents: list[dict]) -> None:
        """Accuracy and reaction time by round, oldest on the left. Rounds with no value leave a gap."""
        data = model.trend(documents)
        palette = self.palette()
        background, text = palette.window().color().name(), palette.windowText().color().name()
        self.figure.clear()
        self.figure.set_facecolor(background)
        top, bottom = self.figure.subplots(2, 1, sharex=True)
        series = ((top, [None if v is None else v * 100 for v in data.accuracy], "Accuracy (%)", (0, 105)),
                  (bottom, data.reaction, "Reaction (s)", None))
        for axis, values, label, limits in series:
            axis.set_facecolor(background)
            axis.plot(range(1, len(values) + 1), [float("nan") if v is None else v for v in values],
                      marker="o", linewidth=1.5)
            axis.set_ylabel(label, color=text)
            axis.tick_params(colors=text)
            axis.grid(True, alpha=0.3)
            known = [v for v in values if v is not None]
            if limits:
                axis.set_ylim(*limits)
            elif known and max(known) > 0:
                axis.set_ylim(0, max(known) * 1.15)  # Headroom so the highest point is not clipped.
            else:
                axis.set_ylim(0, 1)
            for spine in axis.spines.values():
                spine.set_color(text)
        bottom.set_xlabel("Round (oldest to newest)", color=text)
        bottom.xaxis.get_major_locator().set_params(integer=True)
        self.canvas.draw_idle()


def install_error_dialog(log_path: Path) -> None:
    """Show unexpected errors in a message box instead of letting Qt abort the program.

    Install after ``start_logging``: the hook it put in place records the error, then this one tells the player.
    """
    previous = sys.excepthook

    def show(exc_type, exc, traceback) -> None:
        previous(exc_type, exc, traceback)
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            return
        try:
            QMessageBox.critical(None, "MotionPlay", f"Something unexpected went wrong:\n\n{exc}\n\n"
                                 f"The details were saved to {log_path}.")
        except Exception:  # Never let the error dialog itself raise inside the hook.
            pass

    sys.excepthook = show


def run(store: ResultStore, users: UserStore, app: QApplication) -> int:
    """Log in, show the dashboard, and repeat after Log out until the player quits."""
    while True:
        dialog = LoginDialog(users)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.user is None:
            return 0
        window = DashboardWindow(store, dialog.user)
        window.show()
        app.exec()
        if not window.logged_out:
            return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MotionPlay dashboard")
    add_store_arguments(parser)
    parser.set_defaults(store="sqlite")  # Accounts and rounds live in the same SQLite file by default.
    parser.add_argument("--users-db", type=Path, default=DEFAULT_USERS_DB,
                        help="SQLite file holding accounts (default: data/motionplay.db)")
    args = parser.parse_args(argv)
    store = users = None
    try:
        settings = load_settings()
        start_logging(settings, "dashboard", console=False)
        store = store_from_args(args.store, args.file, settings)
        users = open_users(args.users_db)
        app = QApplication.instance() or QApplication(sys.argv[:1])
        install_error_dialog(settings.log_dir / "motionplay.log")
        return run(store, users, app)
    except (ConfigurationError, StorageError, OSError) as error:
        print(f"Dashboard error: {error}", file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.close()
        if users is not None:
            users.close()


if __name__ == "__main__":
    raise SystemExit(main())
