from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtCore import QObject, QThread, QTimer, Slot, Signal
from ui_dome import Ui_MainWindow
from dome_shutter import Dome_Control
import sys

class DomeWorker(QObject):
    finished = Signal()
    east_status = Signal(str)
    east_position = Signal(int)
    west_status = Signal(str)
    west_position = Signal(int)
    error = Signal(str)

    def __init__(self, dome):
        super().__init__()
        self.dome = dome
        self.position_timer = None

    @Slot()
    def start(self):
        self.position_timer = QTimer()
        self.position_timer.timeout.connect(self.dome_position)
        self.position_timer.start(100)

    @Slot()
    def west_open(self):
        self.west_status.emit("WEST OPENING")
        self.dome.open_w()

    @Slot()
    def west_stop(self):
        self.west_status.emit("WEST STOPPING")
        self.dome.stop_w()

    @Slot()
    def west_close(self):
        self.west_status.emit("WEST CLOSING")
        self.dome.close_w()

    @Slot(int)
    def west_goto(self, value):
        self.west_status.emit(f"WEST SETPOINT {value}")
        value = 100 - value  # backwards state - 100 is closed, 0 is open
        self.dome.goto_w(value)

    @Slot()
    def east_open(self):
        self.east_status.emit("EAST OPENING")
        self.dome.open_e()

    @Slot()
    def east_stop(self):
        self.east_status.emit("EAST STOPPING")
        self.dome.stop_e()

    @Slot()
    def east_close(self):
        self.east_status.emit("EAST CLOSING")
        self.dome.close_e()

    @Slot(int)
    def east_goto(self, value):
        self.east_status.emit(f"EAST SETPOINT {value}")
        value = 100 - value  # backwards state - 100 is closed, 0 is open
        self.dome.goto_e(value)

    @Slot()
    def dome_position(self):
        self.east_position.emit(int(self.dome.last_east))
        self.west_position.emit(int(self.dome.last_west))



class DomeWindow(QMainWindow):
    # worker signals
    west_setpoint_requested = Signal(int)
    east_setpoint_requested = Signal(int)
    def __init__(self):
        super().__init__()

        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self.dome = Dome_Control()
        self.worker = DomeWorker(dome=self.dome)
        self.dome_thread = QThread()
        self.worker.moveToThread(self.dome_thread)
        self.dome_thread.started.connect(self.worker.start)
        self.dome_thread.start()
        self.worker.east_status.connect(self.east_status)
        self.worker.west_status.connect(self.west_status)
        self.worker.east_position.connect(self.east_progress_update)
        self.worker.west_position.connect(self.west_progress_update)
        self.west_setpoint_requested.connect(self.worker.west_goto)
        self.east_setpoint_requested.connect(self.worker.east_goto)
        self.ui.west_open.clicked.connect(self.worker.west_open)
        self.ui.west_stop.clicked.connect(self.worker.west_stop)
        self.ui.west_close.clicked.connect(self.worker.west_close)
        self.ui.west_set.clicked.connect(self.west_setpoint)
        self.ui.east_open.clicked.connect(self.worker.east_open)
        self.ui.east_stop.clicked.connect(self.worker.east_stop)
        self.ui.east_close.clicked.connect(self.worker.east_close)
        self.ui.east_set.clicked.connect(self.east_setpoint)
        self.ui.actionConnect_RainMon.triggered.connect(self.connect_rainmon)
        self.ui.actionDisable_RDP_Monitor.triggered.connect(self.toggle_rdp)

    def west_setpoint(self):
        value = self.ui.west_set_pos.value()
        print(f"WEST SETPOINT → {value}")
        # send the dome to the requested position
        self.west_setpoint_requested.emit(value)

    def west_progress_update(self, value):
        self.ui.west_progress.setValue(value)

    def west_status(self, text):
        #self.west_statusBar().showMessage(text)
        print(text)

    def east_setpoint(self):
        value = self.ui.east_set_pos.value()
        print(f"EAST SETPOINT → {value}")
        self.east_setpoint_requested.emit(value)

    def east_progress_update(self, value):
        self.ui.east_progress.setValue(value)

    def east_status(self, text):
        #self.east_statusBar().showMessage(text)
        print(text)

    # ===== MENU =====
    def connect_rainmon(self):
        print("Connecting Rain Monitor...")

    def toggle_rdp(self):
        print("Toggling RDP Monitor")

    def closeEvent(self, event):
        self.dome_thread.quit()
        self.dome_thread.wait()
        super().closeEvent(event)

if __name__ == "__main__":
    app = QApplication(sys.argv)

    window = DomeWindow()
    window.show()

    sys.exit(app.exec())