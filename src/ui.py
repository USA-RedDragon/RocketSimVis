import string
from typing import Optional

from pathlib import Path

import moderngl_window
import moderngl_window.context.pyqt5.window as qtw

from PyQt5 import QtOpenGL, QtWidgets
from PyQt5.QtCore import QSize, Qt, QTimer, QRect
from PyQt5.QtGui import QScreen, QColor, QFontMetrics, QPalette, QBrush
from PyQt5.Qt import QPainter, QWidget, pyqtSlot, QEvent

from config import Config, ConfigVal

from const import WINDOW_SIZE_X, WINDOW_SIZE_Y

_g_ui_widget = None
_g_rewards_panel = None

_g_scaling_factor = 1
def update_scaling_factor(app: QtWidgets.QApplication):
    global _g_scaling_factor

    # Make a test label
    alphabet = string.ascii_lowercase[:14]
    test_label = QtWidgets.QLabel(alphabet)
    test_label.setStyleSheet(app.styleSheet())
    test_label.ensurePolished()

    font_height = test_label.fontMetrics().height()

    _g_scaling_factor = font_height / 13

    print("Scaling factor updated to", _g_scaling_factor)

def get_scaling_factor():
    return _g_scaling_factor

def set_target_size(widget: QtWidgets.QWidget):
    base_size = QSize(*widget.SIZE)
    base_size.setWidth(round(base_size.width() * get_scaling_factor()))
    base_size.setHeight(round(base_size.height() * get_scaling_factor()))
    min_size = widget.sizeHint()

    #if widget.layout() is not None:
    #    min_size = widget.layout().sizeHint()
    #    min_size += QSize(widget.layout().spacing(), widget.layout().spacing()) * 2

    size = QSize(max(base_size.width(), min_size.width()), max(base_size.height(), min_size.height()))
    widget.setFixedSize(size)
    widget.resize(size)

class QConfigVal(QWidget):
    FLOAT_SLIDER_PREC = 100

    def __init__(self, name: str, config_val: ConfigVal):
        QWidget.__init__(self)

        self.name = name
        self.config_val = config_val

        self.setAttribute(Qt.WA_StyledBackground)
        self.setAutoFillBackground(True)

        self.layout = QtWidgets.QVBoxLayout(self)
        self.layout.setAlignment(Qt.AlignTop)

        self.label = QtWidgets.QLabel("...")

        self.slider = QtWidgets.QSlider(Qt.Horizontal, self)
        self.slider.setFixedHeight(round(10 * get_scaling_factor()))

        self.float_mode = (config_val.max - config_val.min) < 10

        if self.float_mode:
            self.slider.setRange(0, self.FLOAT_SLIDER_PREC)
            val_frac = (config_val.val - config_val.min) / (config_val.max - config_val.min)
            self.slider.setValue(round(val_frac * self.FLOAT_SLIDER_PREC))
        else:
            self.slider.setRange(round(config_val.min), round(config_val.max))
            self.slider.setValue(round(config_val.val))

        self.slider.valueChanged.connect(self.on_val_changed)

        self.on_val_changed()

        self.layout.addWidget(self.label)
        self.layout.addWidget(self.slider)

    def get_beautified_name(self):
        bname = self.name.replace('_''', ' ').capitalize()
        return bname

    @pyqtSlot()
    def on_val_changed(self):
        if self.float_mode:
            slider_frac = self.slider.value() / self.FLOAT_SLIDER_PREC
            self.config_val.val = self.config_val.min + (self.config_val.max - self.config_val.min) * slider_frac
        else:
            self.config_val.val = self.slider.value()
        self.label.setText(self.get_beautified_name() + ": " + str(self.config_val.val))

class QEditConfigWidget(QWidget):
    SIZE = (300, 500)

    def __init__(self, config: Config):
        QWidget.__init__(self)

        self.setAttribute(Qt.WA_StyledBackground)
        self.setAutoFillBackground(True)

        self.setLayout(QtWidgets.QVBoxLayout(self))

        self.text_label = QtWidgets.QLabel("Settings:\n")
        self.layout().addWidget(self.text_label)

        self.config = config

        self.camera_group = QtWidgets.QGroupBox("Camera")
        self.camera_group_layout = QtWidgets.QVBoxLayout(self)
        self.camera_group.setLayout(self.camera_group_layout)
        self.layout().addWidget(self.camera_group)

        for name, obj in self.config.__dict__.items():
            if isinstance(obj, ConfigVal):
                config_val = obj # type: ConfigVal

                widget = QConfigVal(name, config_val)

                if name.startswith("camera_"):
                    self.camera_group_layout.addWidget(widget)

        self.footer_label = QtWidgets.QLabel("\n(Click outside this area to close settings)")
        # TODO: Kinda hacky, ideally use setDisabled(True) and add disabled color to stylesheet?
        self.footer_label.setStyleSheet("color: gray")
        self.layout().addWidget(self.footer_label)

        set_target_size(self)

    def update(self):
        super().update()

class QUIBarWidget(QWidget):
    # No fixed SIZE - will be dynamic based on content

    def __init__(self, parent_window):
        QWidget.__init__(self)

        self.config_edit_popup = None

        self.parent_window = parent_window

        self.setAttribute(Qt.WA_StyledBackground)
        self.setAutoFillBackground(True)

        vbox = QtWidgets.QVBoxLayout()
        vbox.setContentsMargins(8, 8, 8, 8)
        vbox.setSpacing(4)

        self.text_label = QtWidgets.QLabel("...")
        self.text_label.setWordWrap(True)
        self.text_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        vbox.addWidget(self.text_label)

        self.edit_config_button = QtWidgets.QPushButton("Edit Settings")
        self.edit_config_button.clicked.connect(self.on_edit_config)
        vbox.addWidget(self.edit_config_button)

        self.setLayout(vbox)

        # Set minimum size but allow growing
        self.setMinimumWidth(round(180 * get_scaling_factor()))

        global _g_ui_widget
        _g_ui_widget = self

    def update(self):
        super().update()

    @pyqtSlot()
    def on_edit_config(self):
        self.parent_window.toggle_edit_config()

    def set_text(self, text: str):
        self.text_label.setText(text)
        # Adjust size to fit content
        self.adjustSize()

def get_ui() -> QUIBarWidget:
    return _g_ui_widget


class QRewardBarWidget(QWidget):
    """A single reward bar with name, instant value, cumulative value, and visual bars"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.name = ""
        self.instant_value = 0.0
        self.cumulative_value = 0.0
        self.max_abs_instant = 1.0  # For scaling the instant bar
        self.max_abs_cumulative = 1.0  # For scaling the cumulative bar
        
        self.setMinimumHeight(round(22 * get_scaling_factor()))
        self.setMinimumWidth(round(350 * get_scaling_factor()))
        
        # Allow horizontal expansion
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        
    def set_data(self, name: str, instant_value: float, cumulative_value: float,
                 max_abs_instant: float = 1.0, max_abs_cumulative: float = 1.0):
        self.name = name
        self.instant_value = instant_value
        self.cumulative_value = cumulative_value
        self.max_abs_instant = max(abs(max_abs_instant), 0.001)
        self.max_abs_cumulative = max(abs(max_abs_cumulative), 0.001)
        self.update()
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        rect = self.rect()
        margin = 2
        bar_height = rect.height() - margin * 2
        available_width = rect.width() - margin * 2
        
        # Dynamic layout based on available width
        # Proportions: name 35%, instant_bar 20%, instant_val 15%, cumul_bar 15%, cumul_val 15%
        name_width = int(available_width * 0.35)
        instant_bar_width = int(available_width * 0.20)
        instant_val_width = int(available_width * 0.15)
        cumul_bar_width = int(available_width * 0.15)
        cumul_val_width = int(available_width * 0.15)
        
        # Draw background
        painter.fillRect(rect, QColor(25, 25, 25))
        
        # Setup font
        painter.setPen(QColor(220, 220, 220))
        font = painter.font()
        font.setPointSize(max(8, round(9 * get_scaling_factor())))
        painter.setFont(font)
        
        x = margin
        
        # Draw name - truncate based on available width
        fm = painter.fontMetrics()
        name_display = self.name
        while fm.horizontalAdvance(name_display) > name_width - 10 and len(name_display) > 3:
            name_display = name_display[:-4] + "..."
        painter.drawText(x, margin, name_width - 5, bar_height, 
                        Qt.AlignLeft | Qt.AlignVCenter, name_display)
        x += name_width
        
        # Draw instant bar (centered at middle of bar area)
        instant_center = x + instant_bar_width // 2
        instant_bar_max = (instant_bar_width // 2) - 2
        clamped_instant = max(-self.max_abs_instant, min(self.max_abs_instant, self.instant_value))
        instant_ratio = clamped_instant / self.max_abs_instant
        instant_bar_w = int(abs(instant_ratio) * instant_bar_max)
        
        # Draw center line
        painter.setPen(QColor(60, 60, 60))
        painter.drawLine(instant_center, margin, instant_center, rect.height() - margin)
        
        # Draw instant bar
        if self.instant_value >= 0:
            bar_color = QColor(60, 180, 60)
            bar_rect = QRect(instant_center, margin + 2, instant_bar_w, bar_height - 4)
        else:
            bar_color = QColor(180, 60, 60)
            bar_rect = QRect(instant_center - instant_bar_w, margin + 2, instant_bar_w, bar_height - 4)
        painter.fillRect(bar_rect, bar_color)
        x += instant_bar_width
        
        # Draw instant value
        painter.setPen(QColor(200, 200, 200))
        instant_str = f"{self.instant_value:+.3f}"
        painter.drawText(x, margin, instant_val_width - 5, bar_height,
                        Qt.AlignRight | Qt.AlignVCenter, instant_str)
        x += instant_val_width
        
        # Draw cumulative bar (same style, different color tint)
        cumul_center = x + cumul_bar_width // 2
        cumul_bar_max = (cumul_bar_width // 2) - 2
        clamped_cumul = max(-self.max_abs_cumulative, min(self.max_abs_cumulative, self.cumulative_value))
        cumul_ratio = clamped_cumul / self.max_abs_cumulative
        cumul_bar_w = int(abs(cumul_ratio) * cumul_bar_max)
        
        # Draw center line
        painter.setPen(QColor(60, 60, 60))
        painter.drawLine(cumul_center, margin, cumul_center, rect.height() - margin)
        
        # Draw cumulative bar (slightly different colors - cyan/magenta)
        if self.cumulative_value >= 0:
            bar_color = QColor(60, 160, 180)  # Cyan-ish
            bar_rect = QRect(cumul_center, margin + 2, cumul_bar_w, bar_height - 4)
        else:
            bar_color = QColor(180, 60, 120)  # Magenta-ish
            bar_rect = QRect(cumul_center - cumul_bar_w, margin + 2, cumul_bar_w, bar_height - 4)
        painter.fillRect(bar_rect, bar_color)
        x += cumul_bar_width
        
        # Draw cumulative value
        painter.setPen(QColor(150, 200, 220))  # Cyan tint to differentiate
        cumul_str = f"{self.cumulative_value:+.2f}"
        painter.drawText(x, margin, cumul_val_width - 5, bar_height,
                        Qt.AlignRight | Qt.AlignVCenter, cumul_str)


class QPlayerRewardsWidget(QWidget):
    """Shows all rewards for a single player"""
    
    TEAM_COLORS = [QColor(40, 80, 160), QColor(180, 100, 40)]  # Blue, Orange
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground)
        self.setAutoFillBackground(True)
        
        # Allow horizontal expansion, minimize vertical
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum)
        
        self.layout = QtWidgets.QVBoxLayout(self)
        self.layout.setContentsMargins(4, 4, 4, 4)
        self.layout.setSpacing(2)
        
        # Header with player info
        self.header = QtWidgets.QLabel("Player")
        self.header.setAlignment(Qt.AlignCenter)
        self.header.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.layout.addWidget(self.header)
        
        # Total reward display (instant + cumulative)
        self.total_label = QtWidgets.QLabel("Instant: 0.000 | Episode: 0.00")
        self.total_label.setAlignment(Qt.AlignCenter)
        self.total_label.setStyleSheet("font-weight: bold;")
        self.total_label.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.layout.addWidget(self.total_label)
        
        # Column headers - use horizontal layout for proper proportional sizing
        self.column_header_widget = QWidget()
        self.column_header_widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.column_header_widget.setStyleSheet("QWidget { border: none; background: transparent; }")
        column_header_layout = QtWidgets.QHBoxLayout(self.column_header_widget)
        column_header_layout.setContentsMargins(2, 0, 2, 0)
        column_header_layout.setSpacing(0)
        
        # Create header labels matching bar proportions (35%, 20%, 15%, 15%, 15%)
        header_style = "color: #888; font-size: 9pt; border: none; background: transparent;"
        
        name_header = QtWidgets.QLabel("")
        name_header.setStyleSheet(header_style)
        column_header_layout.addWidget(name_header, 35)  # 35% stretch
        
        instant_header = QtWidgets.QLabel("Instant")
        instant_header.setStyleSheet(header_style)
        instant_header.setAlignment(Qt.AlignCenter)
        column_header_layout.addWidget(instant_header, 35)  # 20% + 15% = 35% for both bar and value
        
        episode_header = QtWidgets.QLabel("Episode")
        episode_header.setStyleSheet(header_style)
        episode_header.setAlignment(Qt.AlignCenter)
        column_header_layout.addWidget(episode_header, 30)  # 15% + 15% = 30% for both bar and value
        
        self.layout.addWidget(self.column_header_widget)
        
        # Container for reward bars
        self.bars_container = QWidget()
        self.bars_container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum)
        self.bars_container.setStyleSheet("QWidget { border: none; }")
        self.bars_layout = QtWidgets.QVBoxLayout(self.bars_container)
        self.bars_layout.setContentsMargins(0, 0, 0, 0)
        self.bars_layout.setSpacing(1)
        self.layout.addWidget(self.bars_container)
        
        self.reward_bars: list[QRewardBarWidget] = []
        self.car_id = -1
        self.team_num = 0
        
    def set_player_data(self, car_id: int, team_num: int, rewards: list, 
                       total_reward: float, cumulative_rewards: dict, cumulative_total: float):
        self.car_id = car_id
        self.team_num = team_num
        
        # Update header
        team_name = "Blue" if team_num == 0 else "Orange"
        self.header.setText(f"Player {car_id} ({team_name})")
        
        # Set header background color based on team
        team_color = self.TEAM_COLORS[team_num] if team_num < 2 else QColor(80, 80, 80)
        self.header.setStyleSheet(f"background-color: rgb({team_color.red()}, {team_color.green()}, {team_color.blue()}); padding: 2px; border-radius: 2px;")
        
        # Update total (instant + cumulative)
        sign_i = "+" if total_reward >= 0 else ""
        sign_c = "+" if cumulative_total >= 0 else ""
        self.total_label.setText(f"Instant: {sign_i}{total_reward:.3f}  |  Episode: {sign_c}{cumulative_total:.2f}")
        
        # Find max absolute values for scaling bars
        max_abs_instant = 0.1
        max_abs_cumulative = 0.1
        for reward in rewards:
            max_abs_instant = max(max_abs_instant, abs(reward.value))
        for val in cumulative_rewards.values():
            max_abs_cumulative = max(max_abs_cumulative, abs(val))
        
        # Ensure we have enough bar widgets
        while len(self.reward_bars) < len(rewards):
            bar = QRewardBarWidget(self.bars_container)
            self.bars_layout.addWidget(bar)
            self.reward_bars.append(bar)
        
        # Hide extra bars
        for i in range(len(rewards), len(self.reward_bars)):
            self.reward_bars[i].hide()
        
        # Update visible bars
        for i, reward in enumerate(rewards):
            cumul_val = cumulative_rewards.get(reward.name, 0.0)
            self.reward_bars[i].set_data(
                reward.name, 
                reward.value, 
                cumul_val,
                max_abs_instant,
                max_abs_cumulative
            )
            self.reward_bars[i].show()
        
        self.adjustSize()


class QRewardsPanelWidget(QWidget):
    """Panel showing per-player reward information - resizable, movable, collapsible"""
    
    RESIZE_MARGIN = 8  # Pixels from edge to trigger resize
    TITLE_HEIGHT = 28  # Height of the title bar for dragging
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground)
        self.setAutoFillBackground(True)
        
        # Enable mouse tracking for resize cursor
        self.setMouseTracking(True)
        
        # Interaction state
        self._resizing = False
        self._moving = False
        self._resize_edge = None  # 'left', 'right', 'top', 'bottom', or corners like 'top-left'
        self._drag_start_pos = None
        self._drag_start_geometry = None
        self._collapsed = False
        self._expanded_height = 0  # Remember height when collapsing
        
        self.layout = QtWidgets.QVBoxLayout(self)
        self.layout.setContentsMargins(6, 6, 6, 6)
        self.layout.setSpacing(4)
        
        # Title bar with collapse button
        self.title_bar = QWidget()
        self.title_bar.setFixedHeight(round(self.TITLE_HEIGHT * get_scaling_factor()))
        self.title_bar.setCursor(Qt.SizeAllCursor)  # Indicate movable
        self.title_bar.setStyleSheet("QWidget { background: transparent; border: none; }")
        title_layout = QtWidgets.QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(4, 0, 4, 0)
        title_layout.setSpacing(4)
        
        # Collapse button
        self.collapse_btn = QtWidgets.QPushButton("-")
        self.collapse_btn.setFixedSize(round(20 * get_scaling_factor()), round(20 * get_scaling_factor()))
        self.collapse_btn.setStyleSheet("QPushButton { font-size: 12pt; font-weight: bold; padding: 0; padding-bottom: 2px; }")
        self.collapse_btn.clicked.connect(self._toggle_collapse)
        self.collapse_btn.setCursor(Qt.PointingHandCursor)
        title_layout.addWidget(self.collapse_btn)
        
        # Title
        self.title = QtWidgets.QLabel("Rewards")
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setStyleSheet("font-weight: bold; font-size: 11pt; border: none; background: transparent;")
        title_layout.addWidget(self.title, 1)  # Stretch to fill
        
        # Spacer to balance the collapse button
        spacer = QWidget()
        spacer.setFixedWidth(round(20 * get_scaling_factor()))
        title_layout.addWidget(spacer)
        
        self.layout.addWidget(self.title_bar)
        
        # Content container (for collapse functionality)
        self.content_widget = QWidget()
        self.content_widget.setStyleSheet("QWidget { border: none; }")
        self.content_layout = QtWidgets.QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(4)
        
        # Scroll area for player widgets
        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setStyleSheet("QScrollArea { border: none; }")
        
        # Container for player reward widgets
        self.players_container = QWidget()
        self.players_container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum)
        self.players_container.setStyleSheet("QWidget { border: none; }")
        self.players_layout = QtWidgets.QVBoxLayout(self.players_container)
        self.players_layout.setContentsMargins(0, 0, 0, 0)
        self.players_layout.setSpacing(6)
        self.scroll_area.setWidget(self.players_container)
        
        self.content_layout.addWidget(self.scroll_area)
        self.layout.addWidget(self.content_widget)
        
        self.player_widgets: list[QPlayerRewardsWidget] = []
        
        # Set minimum and default size
        self.setMinimumWidth(round(300 * get_scaling_factor()))
        self.setMinimumHeight(round(50 * get_scaling_factor()))  # Smaller min for collapsed state
        self._default_width = round(550 * get_scaling_factor())
        self._default_height = round(500 * get_scaling_factor())
        self.resize(self._default_width, self._default_height)
        self._first_show = True
        self._auto_sized = False  # Track if we've auto-sized to content
        
        global _g_rewards_panel
        _g_rewards_panel = self
    
    def _toggle_collapse(self):
        """Toggle the collapsed state of the panel"""
        self._collapsed = not self._collapsed
        if self._collapsed:
            self._expanded_height = self.height()
            self.content_widget.hide()
            self.collapse_btn.setText("+")
            # Resize to just show title bar
            collapsed_height = self.title_bar.height() + self.layout.contentsMargins().top() + self.layout.contentsMargins().bottom() + 4
            self.setFixedHeight(collapsed_height)
        else:
            self.setMinimumHeight(round(50 * get_scaling_factor()))
            self.setMaximumHeight(16777215)  # Reset to default max
            self.content_widget.show()
            self.collapse_btn.setText("-")
            # Restore previous height
            if self._expanded_height > 0:
                self.resize(self.width(), self._expanded_height)
    
    def showEvent(self, event):
        """Position panel when first shown"""
        super().showEvent(event)
        if self._first_show and self.parent():
            self._first_show = False
            parent = self.parent()
            # Position at top-right of parent
            new_x = parent.width() - self.width() - 10
            self.move(max(10, new_x), 10)
    
    def _get_resize_edge(self, pos):
        """Determine which edge/corner the mouse is near"""
        if self._collapsed:
            return None  # No resizing when collapsed
            
        margin = self.RESIZE_MARGIN
        rect = self.rect()
        
        near_left = pos.x() < margin
        near_right = pos.x() > rect.width() - margin
        near_top = pos.y() < margin
        near_bottom = pos.y() > rect.height() - margin
        
        # Corners first
        if near_top and near_left:
            return 'top-left'
        if near_top and near_right:
            return 'top-right'
        if near_bottom and near_left:
            return 'bottom-left'
        if near_bottom and near_right:
            return 'bottom-right'
        # Edges
        if near_left:
            return 'left'
        if near_right:
            return 'right'
        if near_top:
            return 'top'
        if near_bottom:
            return 'bottom'
        return None
    
    def _is_in_title_bar(self, pos):
        """Check if position is in the title bar (for dragging)"""
        return pos.y() < self.title_bar.height() + self.layout.contentsMargins().top()
    
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # Check for resize first
            edge = self._get_resize_edge(event.pos())
            if edge:
                self._resizing = True
                self._resize_edge = edge
                self._drag_start_pos = event.globalPos()
                self._drag_start_geometry = self.geometry()
                event.accept()
                return
            
            # Check for title bar drag (move)
            if self._is_in_title_bar(event.pos()):
                self._moving = True
                self._drag_start_pos = event.globalPos()
                self._drag_start_geometry = self.geometry()
                event.accept()
                return
                
        super().mousePressEvent(event)
    
    def mouseMoveEvent(self, event):
        if self._resizing and self._drag_start_pos:
            delta = event.globalPos() - self._drag_start_pos
            geom = QRect(self._drag_start_geometry)
            
            # Handle horizontal resizing
            if 'left' in self._resize_edge:
                new_width = geom.width() - delta.x()
                new_width = max(new_width, self.minimumWidth())
                new_left = geom.right() - new_width + 1
                geom.setLeft(new_left)
            elif 'right' in self._resize_edge:
                new_width = geom.width() + delta.x()
                new_width = max(new_width, self.minimumWidth())
                geom.setWidth(new_width)
            
            # Handle vertical resizing
            if 'top' in self._resize_edge:
                new_height = geom.height() - delta.y()
                new_height = max(new_height, self.minimumHeight())
                new_top = geom.bottom() - new_height + 1
                geom.setTop(new_top)
            elif 'bottom' in self._resize_edge:
                new_height = geom.height() + delta.y()
                new_height = max(new_height, self.minimumHeight())
                geom.setHeight(new_height)
            
            self.setGeometry(geom)
            event.accept()
            return
        elif self._moving and self._drag_start_pos:
            delta = event.globalPos() - self._drag_start_pos
            new_pos = self._drag_start_geometry.topLeft() + delta
            
            # Constrain to parent bounds
            if self.parent():
                parent_rect = self.parent().rect()
                new_pos.setX(max(0, min(new_pos.x(), parent_rect.width() - self.width())))
                new_pos.setY(max(0, min(new_pos.y(), parent_rect.height() - self.height())))
            
            self.move(new_pos)
            event.accept()
            return
        else:
            # Update cursor based on position
            edge = self._get_resize_edge(event.pos())
            if edge in ('top-left', 'bottom-right'):
                self.setCursor(Qt.SizeFDiagCursor)
            elif edge in ('top-right', 'bottom-left'):
                self.setCursor(Qt.SizeBDiagCursor)
            elif edge in ('left', 'right'):
                self.setCursor(Qt.SizeHorCursor)
            elif edge in ('top', 'bottom'):
                self.setCursor(Qt.SizeVerCursor)
            elif self._is_in_title_bar(event.pos()):
                self.setCursor(Qt.SizeAllCursor)
            else:
                self.setCursor(Qt.ArrowCursor)
        
        super().mouseMoveEvent(event)
    
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self._resizing or self._moving:
                self._resizing = False
                self._moving = False
                self._resize_edge = None
                self._drag_start_pos = None
                self._drag_start_geometry = None
                event.accept()
                return
        super().mouseReleaseEvent(event)
    
    def leaveEvent(self, event):
        self.setCursor(Qt.ArrowCursor)
        super().leaveEvent(event)
    
    def update_rewards(self, car_states, spectate_idx: int):
        """
        Update the rewards panel with current state.
        
        Args:
            car_states: List of CarState objects with reward info
            spectate_idx: Index of the spectated car, or -1 for arena cam (show all)
        """
        # Determine which players to show
        if spectate_idx >= 0 and spectate_idx < len(car_states):
            # Following a specific player - show only that player
            players_to_show = [car_states[spectate_idx]]
        else:
            # Arena cam or no valid spectate - show all players
            players_to_show = car_states
        
        # Filter out players with no reward data
        players_to_show = [p for p in players_to_show if p.player_rewards.rewards]
        
        if not players_to_show:
            self.hide()
            return
        
        self.show()
        
        # Ensure we have enough player widgets
        while len(self.player_widgets) < len(players_to_show):
            widget = QPlayerRewardsWidget(self.players_container)
            self.players_layout.addWidget(widget)
            self.player_widgets.append(widget)
        
        # Hide extra widgets
        for i in range(len(players_to_show), len(self.player_widgets)):
            self.player_widgets[i].hide()
        
        # Update visible widgets
        for i, car_state in enumerate(players_to_show):
            self.player_widgets[i].set_player_data(
                car_id=car_state.car_id,
                team_num=car_state.team_num,
                rewards=car_state.player_rewards.rewards,
                total_reward=car_state.player_rewards.total_reward,
                cumulative_rewards=car_state.player_rewards.cumulative_rewards,
                cumulative_total=car_state.player_rewards.cumulative_total
            )
            self.player_widgets[i].show()
        
        # Auto-size to fit content on first data update (but respect user resizing after)
        if not self._auto_sized and not self._collapsed:
            self._auto_sized = True
            # Calculate ideal size based on content
            self.players_container.adjustSize()
            content_hint = self.players_container.sizeHint()
            
            # Add margins and title bar
            margins = self.layout.contentsMargins()
            ideal_height = (content_hint.height() + 
                           self.title_bar.height() + 
                           margins.top() + margins.bottom() + 
                           self.content_layout.spacing() + 20)  # Extra padding
            ideal_width = max(content_hint.width() + margins.left() + margins.right() + 20,
                             self.minimumWidth())
            
            # Constrain to reasonable bounds
            if self.parent():
                max_height = self.parent().height() - 40
                max_width = self.parent().width() - 40
                ideal_height = min(ideal_height, max_height)
                ideal_width = min(ideal_width, max_width)
            
            ideal_height = max(ideal_height, round(200 * get_scaling_factor()))
            
            self.resize(ideal_width, ideal_height)
            
            # Reposition to top-right after resize
            if self.parent():
                new_x = self.parent().width() - self.width() - 10
                self.move(max(10, new_x), 10)


def get_rewards_panel() -> Optional[QRewardsPanelWidget]:
    return _g_rewards_panel

class QRSVWindow(QtWidgets.QMainWindow):
    def __init__(self, gl_widget):
        super().__init__()

        self.setWindowTitle("RocketSimVis")

        path = Path(__file__).parent.resolve() / "qt_style_sheet.css"
        self.setStyleSheet(path.read_text())

        # Set the central widget of the Window.
        self.gl_widget = gl_widget
        self.setCentralWidget(self.gl_widget)

        self.base_layout = QtWidgets.QVBoxLayout(self)

        # Info bar widget (top-left)
        self.bar_widget = QUIBarWidget(self)
        self.layout().addWidget(self.bar_widget)

        # Rewards panel (top-right)
        self.rewards_panel = QRewardsPanelWidget(self)
        self.layout().addWidget(self.rewards_panel)
        self.rewards_panel.hide()  # Hidden until we receive reward data

        self.edit_config_widget = QEditConfigWidget(self.gl_widget.config)
        self.layout().addWidget(self.edit_config_widget)
        self.edit_config_widget.hide()

        self.resize(WINDOW_SIZE_X, WINDOW_SIZE_Y)

        self.installEventFilter(self)
        self.centralWidget().installEventFilter(self)
        
    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Keep rewards panel within window bounds and anchored to top-right
        if hasattr(self, 'rewards_panel') and self.rewards_panel.isVisible():
            panel = self.rewards_panel
            panel_width = panel.width()
            panel_height = panel.height()
            
            # Anchor to top-right corner with margin
            new_x = self.width() - panel_width - 10
            new_y = 10
            
            # Ensure panel doesn't go off screen
            new_x = max(10, new_x)
            
            # Constrain height if window got smaller
            max_height = self.height() - 20
            if panel_height > max_height:
                panel.resize(panel_width, max_height)
            
            panel.move(new_x, new_y)
        
    def _position_widgets(self):
        """Position floating widgets after resize"""
        # Position rewards panel at top-right
        if self.rewards_panel.isVisible():
            panel_width = self.rewards_panel.sizeHint().width()
            panel_height = self.rewards_panel.sizeHint().height()
            self.rewards_panel.setGeometry(
                self.width() - panel_width - 10,
                10,
                panel_width,
                min(panel_height, self.height() - 20)
            )

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonPress:
            if event.button() == Qt.LeftButton:
                press_pos = event.pos()

                # Close config window if we click outside of it
                if self.edit_config_widget.isVisible():
                    if not (press_pos in self.edit_config_widget.geometry()):
                        self.toggle_edit_config()
        elif event.type() == QEvent.KeyPress:
            self.gl_widget.keyPressEvent(event)

        return super().eventFilter(obj, event)

    def toggle_edit_config(self):
        if not self.edit_config_widget.isVisible():
            self.edit_config_widget.show()

            size = self.edit_config_widget.size()

            # Don't exceed our window size
            size.setWidth(min(size.width(), self.width()))
            size.setHeight(min(size.height(), self.height()))

            self.edit_config_widget.setFixedSize(size)

            self.edit_config_widget.setGeometry(
                0, self.bar_widget.height() + 20,
                size.width(), size.width()
            )
        else:
            self.edit_config_widget.hide()