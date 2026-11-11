"""Start Tile Mapper:  python -m tilemapper"""
import sys


def main():
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication
    from . import APP_NAME
    from .app.main_window import MainWindow

    class App(QApplication):
        """Handles Finder 'open this .tmap' requests."""
        window = None
        pending = None

        def event(self, e):
            if e.type() == QEvent.Type.FileOpen:
                path = e.file()
                if path.endswith(".tmap"):
                    if self.window:
                        self.window.open_map(path)
                    else:
                        self.pending = path
                return True
            return super().event(e)

    app = App(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("TileMapper")
    app.setOrganizationDomain("tilemapper.local")
    w = MainWindow()
    app.window = w
    w.show()
    for arg in sys.argv[1:]:
        if arg.endswith(".tmap"):
            app.pending = arg
    if app.pending:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(300, lambda: w.open_map(app.pending))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
