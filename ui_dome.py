# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'Dome.ui'
##
## Created by: Qt User Interface Compiler version 6.11.1
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PySide6.QtGui import (QAction, QBrush, QColor, QConicalGradient,
    QCursor, QFont, QFontDatabase, QGradient,
    QIcon, QImage, QKeySequence, QLinearGradient,
    QPainter, QPalette, QPixmap, QRadialGradient,
    QTransform)
from PySide6.QtWidgets import (QApplication, QLabel, QMainWindow, QMenu,
    QMenuBar, QProgressBar, QPushButton, QSizePolicy,
    QSlider, QStatusBar, QWidget)

from wind_vane import WindVane

class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        if not MainWindow.objectName():
            MainWindow.setObjectName(u"MainWindow")
        MainWindow.resize(600, 300)
        palette = QPalette()
        brush = QBrush(QColor(60, 90, 50, 255))
        brush.setStyle(Qt.BrushStyle.SolidPattern)
        palette.setBrush(QPalette.ColorGroup.Active, QPalette.ColorRole.BrightText, brush)
        brush1 = QBrush(QColor(60, 110, 50, 255))
        brush1.setStyle(Qt.BrushStyle.SolidPattern)
        palette.setBrush(QPalette.ColorGroup.Active, QPalette.ColorRole.Highlight, brush1)
        brush2 = QBrush(QColor(179, 121, 69, 255))
        brush2.setStyle(Qt.BrushStyle.SolidPattern)
        palette.setBrush(QPalette.ColorGroup.Active, QPalette.ColorRole.Link, brush2)
        brush3 = QBrush(QColor(116, 82, 33, 255))
        brush3.setStyle(Qt.BrushStyle.SolidPattern)
        palette.setBrush(QPalette.ColorGroup.Active, QPalette.ColorRole.LinkVisited, brush3)
#if QT_VERSION >= QT_VERSION_CHECK(6, 6, 0)
        palette.setBrush(QPalette.ColorGroup.Active, QPalette.ColorRole.Accent, brush)
#endif
        palette.setBrush(QPalette.ColorGroup.Inactive, QPalette.ColorRole.BrightText, brush)
        palette.setBrush(QPalette.ColorGroup.Inactive, QPalette.ColorRole.Highlight, brush1)
        palette.setBrush(QPalette.ColorGroup.Inactive, QPalette.ColorRole.Link, brush2)
        palette.setBrush(QPalette.ColorGroup.Inactive, QPalette.ColorRole.LinkVisited, brush3)
#if QT_VERSION >= QT_VERSION_CHECK(6, 6, 0)
        palette.setBrush(QPalette.ColorGroup.Inactive, QPalette.ColorRole.Accent, brush)
#endif
        palette.setBrush(QPalette.ColorGroup.Disabled, QPalette.ColorRole.BrightText, brush)
        palette.setBrush(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Link, brush2)
        palette.setBrush(QPalette.ColorGroup.Disabled, QPalette.ColorRole.LinkVisited, brush3)
#if QT_VERSION >= QT_VERSION_CHECK(6, 6, 0)
        palette.setBrush(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Accent, brush)
#endif
        MainWindow.setPalette(palette)
        self.actionDisable_RDP_Monitor = QAction(MainWindow)
        self.actionDisable_RDP_Monitor.setObjectName(u"actionDisable_RDP_Monitor")
        self.actionDisable_RDP_Monitor.setCheckable(False)
        self.actionDisable_RDP_Monitor.setEnabled(True)
        self.actionConnect_RainMon = QAction(MainWindow)
        self.actionConnect_RainMon.setObjectName(u"actionConnect_RainMon")
        self.centralwidget = QWidget(MainWindow)
        self.centralwidget.setObjectName(u"centralwidget")
        self.west_open = QPushButton(self.centralwidget)
        self.west_open.setObjectName(u"west_open")
        self.west_open.setGeometry(QRect(30, 130, 80, 30))
        self.west_stop = QPushButton(self.centralwidget)
        self.west_stop.setObjectName(u"west_stop")
        self.west_stop.setGeometry(QRect(30, 90, 80, 30))
        self.west_close = QPushButton(self.centralwidget)
        self.west_close.setObjectName(u"west_close")
        self.west_close.setGeometry(QRect(30, 50, 80, 30))
        self.east_open = QPushButton(self.centralwidget)
        self.east_open.setObjectName(u"east_open")
        self.east_open.setGeometry(QRect(490, 130, 80, 30))
        self.east_stop = QPushButton(self.centralwidget)
        self.east_stop.setObjectName(u"east_stop")
        self.east_stop.setGeometry(QRect(490, 90, 80, 30))
        self.east_close = QPushButton(self.centralwidget)
        self.east_close.setObjectName(u"east_close")
        self.east_close.setGeometry(QRect(490, 50, 80, 30))
        self.west_label = QLabel(self.centralwidget)
        self.west_label.setObjectName(u"west_label")
        self.west_label.setGeometry(QRect(30, 10, 80, 30))
        self.west_label.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.west_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.east_label = QLabel(self.centralwidget)
        self.east_label.setObjectName(u"east_label")
        self.east_label.setGeometry(QRect(490, 10, 80, 30))
        self.east_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.Wind = WindVane(self.centralwidget)
        self.Wind.setObjectName(u"Wind")
        self.Wind.setGeometry(QRect(200, 30, 200, 200))
        self.label = QLabel(self.centralwidget)
        self.label.setObjectName(u"label")
        self.label.setGeometry(QRect(275, 0, 50, 20))
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.west_progress = QProgressBar(self.centralwidget)
        self.west_progress.setObjectName(u"west_progress")
        self.west_progress.setGeometry(QRect(160, 30, 30, 171))
        self.west_progress.setStyleSheet("""
                    QProgressBar {
                        border: 1px solid gray;
                        text-align: center;
                        font-size: 7pt;
                    }
                    QProgressBar::chunk {
                        background-color: #3c5a32;
                    }
                    """)
        sizePolicy = QSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Expanding)
        sizePolicy.setHorizontalStretch(100)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.west_progress.sizePolicy().hasHeightForWidth())
        self.west_progress.setSizePolicy(sizePolicy)
        self.west_progress.setValue(0)
        self.west_progress.setOrientation(Qt.Orientation.Vertical)
        self.west_progress.setInvertedAppearance(False)
        self.east_progress = QProgressBar(self.centralwidget)
        self.east_progress.setObjectName(u"east_progress")
        self.east_progress.setGeometry(QRect(410, 30, 30, 171))
        self.east_progress.setStyleSheet("""
                            QProgressBar {
                                border: 1px solid gray;
                                text-align: center;
                                font-size: 7pt;
                            }
                            QProgressBar::chunk {
                                background-color: #3c5a32;
                            }
                            """)
        sizePolicy1 = QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        sizePolicy1.setHorizontalStretch(0)
        sizePolicy1.setVerticalStretch(0)
        sizePolicy1.setHeightForWidth(self.east_progress.sizePolicy().hasHeightForWidth())
        self.east_progress.setSizePolicy(sizePolicy1)
        self.east_progress.setBaseSize(QSize(0, 0))
        palette1 = QPalette()
        palette1.setBrush(QPalette.ColorGroup.Active, QPalette.ColorRole.BrightText, brush)
        brush4 = QBrush(QColor(60, 120, 50, 255))
        brush4.setStyle(Qt.BrushStyle.SolidPattern)
        palette1.setBrush(QPalette.ColorGroup.Active, QPalette.ColorRole.Highlight, brush4)
        palette1.setBrush(QPalette.ColorGroup.Active, QPalette.ColorRole.Link, brush2)
        palette1.setBrush(QPalette.ColorGroup.Active, QPalette.ColorRole.LinkVisited, brush3)
#if QT_VERSION >= QT_VERSION_CHECK(6, 6, 0)
        palette1.setBrush(QPalette.ColorGroup.Active, QPalette.ColorRole.Accent, brush)
#endif
        palette1.setBrush(QPalette.ColorGroup.Inactive, QPalette.ColorRole.BrightText, brush)
        palette1.setBrush(QPalette.ColorGroup.Inactive, QPalette.ColorRole.Highlight, brush4)
        palette1.setBrush(QPalette.ColorGroup.Inactive, QPalette.ColorRole.Link, brush2)
        palette1.setBrush(QPalette.ColorGroup.Inactive, QPalette.ColorRole.LinkVisited, brush3)
#if QT_VERSION >= QT_VERSION_CHECK(6, 6, 0)
        palette1.setBrush(QPalette.ColorGroup.Inactive, QPalette.ColorRole.Accent, brush)
#endif
        palette1.setBrush(QPalette.ColorGroup.Disabled, QPalette.ColorRole.BrightText, brush)
        palette1.setBrush(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Link, brush2)
        palette1.setBrush(QPalette.ColorGroup.Disabled, QPalette.ColorRole.LinkVisited, brush3)
#if QT_VERSION >= QT_VERSION_CHECK(6, 6, 0)
        palette1.setBrush(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Accent, brush)
#endif
        self.east_progress.setPalette(palette1)
        self.east_progress.setValue(0)
        self.east_progress.setOrientation(Qt.Orientation.Vertical)
        self.east_progress.setInvertedAppearance(False)
        self.west_set_pos = QSlider(self.centralwidget)
        self.west_set_pos.setObjectName(u"west_set_pos")
        self.west_set_pos.setGeometry(QRect(130, 20, 20, 191))
        self.west_set_pos.setMaximum(100)
        self.west_set_pos.setSliderPosition(0)
        self.west_set_pos.setOrientation(Qt.Orientation.Vertical)
        self.west_set_pos.setInvertedAppearance(False)
        self.west_set_pos.setInvertedControls(False)
        self.west_set_pos.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.west_set_pos.setTickInterval(25)
        self.east_set_pos = QSlider(self.centralwidget)
        self.east_set_pos.setObjectName(u"east_set_pos")
        self.east_set_pos.setGeometry(QRect(450, 20, 20, 191))
        self.east_set_pos.setMaximum(100)
        self.east_set_pos.setSliderPosition(0)
        self.east_set_pos.setOrientation(Qt.Orientation.Vertical)
        self.east_set_pos.setTickPosition(QSlider.TickPosition.TicksAbove)
        self.east_set_pos.setTickInterval(25)
        self.west_set = QPushButton(self.centralwidget)
        self.west_set.setObjectName(u"west_set")
        self.west_set.setGeometry(QRect(30, 170, 80, 30))
        self.east_set = QPushButton(self.centralwidget)
        self.east_set.setObjectName(u"east_set")
        self.east_set.setGeometry(QRect(490, 170, 80, 30))
        MainWindow.setCentralWidget(self.centralwidget)
        self.menubar = QMenuBar(MainWindow)
        self.menubar.setObjectName(u"menubar")
        self.menubar.setGeometry(QRect(0, 0, 600, 33))
        self.menufsgsdg = QMenu(self.menubar)
        self.menufsgsdg.setObjectName(u"menufsgsdg")
        MainWindow.setMenuBar(self.menubar)
        self.statusbar = QStatusBar(MainWindow)
        self.statusbar.setObjectName(u"statusbar")
        MainWindow.setStatusBar(self.statusbar)

        self.menubar.addAction(self.menufsgsdg.menuAction())
        self.menufsgsdg.addAction(self.actionDisable_RDP_Monitor)
        self.menufsgsdg.addSeparator()
        self.menufsgsdg.addAction(self.actionConnect_RainMon)
        self.menufsgsdg.addSeparator()

        self.retranslateUi(MainWindow)

        QMetaObject.connectSlotsByName(MainWindow)
    # setupUi

    def retranslateUi(self, MainWindow):
        MainWindow.setWindowTitle(QCoreApplication.translate("MainWindow", u"MainWindow", None))
        self.actionDisable_RDP_Monitor.setText(QCoreApplication.translate("MainWindow", u"Disable RDP Monitor", None))
        self.actionConnect_RainMon.setText(QCoreApplication.translate("MainWindow", u"Connect RainMon", None))
        self.west_open.setText(QCoreApplication.translate("MainWindow", u"Open", None))
        self.west_stop.setText(QCoreApplication.translate("MainWindow", u"Stop", None))
        self.west_close.setText(QCoreApplication.translate("MainWindow", u"Close", None))
        self.east_open.setText(QCoreApplication.translate("MainWindow", u"Open", None))
        self.east_stop.setText(QCoreApplication.translate("MainWindow", u"Stop", None))
        self.east_close.setText(QCoreApplication.translate("MainWindow", u"Close", None))
        self.west_label.setText(QCoreApplication.translate("MainWindow", u"West", None))
        self.east_label.setText(QCoreApplication.translate("MainWindow", u"East", None))
        self.label.setText(QCoreApplication.translate("MainWindow", u"Wind", None))
        self.west_set.setText(QCoreApplication.translate("MainWindow", u"To Setpoint", None))
        self.east_set.setText(QCoreApplication.translate("MainWindow", u"To Setpoint", None))
        self.menufsgsdg.setTitle(QCoreApplication.translate("MainWindow", u"Options", None))
    # retranslateUi

