"""Semantic palette and widget styling shared by every screen."""
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from .localization import active_language

LIGHT = dict(bg='#F5F3EF', surface='#FFFFFF', recessed='#EEEBE6', line='#DDD8D1',
             border='#8B847C', border_hover='#635D57', primary='#68445F',
             primary_hover='#593850', primary_pressed='#492E42', on_primary='#FFFFFF',
             hover='#F0ECE7', pressed='#E5DFD8', selected='#EDE3EA', selected_text='#593850',
             focus='#79506E', text='#292724', secondary='#59534D', muted='#70685F',
             disabled='#E9E5DF', disabled_text='#827A72', success='#276343', success_bg='#EDF5EE',
             warning='#805400', warning_bg='#FFF4DA', error='#AA303A', error_bg='#FCEEF0',
             selection='#E2CEE0', selection_text='#292724', focus_surface='#FBF7FA')
DARK = dict(bg='#201E20', surface='#292629', recessed='#242124', line='#433D43',
            border='#817681', border_hover='#A99AA7', primary='#D4AECA',
            primary_hover='#E2C1D9', primary_pressed='#C296B6', on_primary='#2D2029',
            hover='#3B353A', pressed='#463D44', selected='#473440', selected_text='#EBC7E1',
            focus='#EBC7E1', text='#F1ECEF', secondary='#C5BCC2', muted='#AEA3AB',
            disabled='#343034', disabled_text='#958B93', success='#9AD4AD', success_bg='#24372B',
            warning='#F0CA7D', warning_bg='#3C3220', error='#FFACB5', error_bg='#402A30',
            selection='#64485D', selection_text='#FFFFFF', focus_surface='#352B33')

def apply_theme(app, dark=False):
    c = DARK if dark else LIGHT
    families = QFontDatabase.families()
    preferred = ('Segoe UI', 'Tahoma', 'Arial') if active_language() == 'ar' else (
        'Segoe UI Variable', 'Segoe UI Variable Text', 'Segoe UI',
    )
    family = next((f for f in preferred if f in families), preferred[-1])
    font = QFont(family)
    font.setPixelSize(14)
    app.setFont(font)
    palette = QPalette()
    for role, key in [(QPalette.Window, 'bg'), (QPalette.WindowText, 'text'), (QPalette.Base, 'surface'),
                      (QPalette.Text, 'text'), (QPalette.Button, 'surface'), (QPalette.ButtonText, 'text'),
                      (QPalette.Highlight, 'selection'), (QPalette.HighlightedText, 'selection_text'),
                      (QPalette.PlaceholderText, 'muted')]:
        palette.setColor(role, QColor(c[key]))
    app.setPalette(palette)
    app.setStyleSheet('''
    QWidget { color: %(text)s; font-family: "%(family)s"; font-size: 14px; }
    QMainWindow, QWidget#page, QWidget#content { background: %(bg)s; }
    QLabel { background: transparent; border: none; }
    QLabel[role="title"] { font-size: 24px; font-weight: 600; min-height: 32px; }
    QLabel[role="heading"] { font-size: 16px; font-weight: 600; min-height: 24px; }
    QLabel[role="field"] { font-size: 13px; font-weight: 600; min-height: 20px; }
    QLabel[role="muted"] { font-size: 12px; color: %(muted)s; min-height: 18px; }
    QLabel[role="secondary"] { color: %(secondary)s; min-height: 22px; }
    QFrame#sidebar { background: %(recessed)s; border: none; }
    QFrame#panel { background: %(surface)s; border: 1px solid %(line)s; border-radius: 8px; }
    QPushButton, QComboBox, QLineEdit, QSpinBox { background: %(surface)s; border: 1px solid %(border)s;
        border-radius: 6px; min-height: 36px; padding: 0 12px; }
    QPushButton { font-weight: 600; padding-left: 16px; padding-right: 16px; }
    QPushButton:hover, QComboBox:hover, QSpinBox:hover { background: %(hover)s; border-color: %(border_hover)s; }
    QPushButton:pressed { background: %(pressed)s; }
    QPushButton:focus { border: 2px solid %(focus)s; }
    QComboBox:focus, QComboBox:on, QLineEdit:focus, QSpinBox:focus { border: 2px solid %(focus)s; background: %(focus_surface)s; }
    QPushButton:disabled, QComboBox:disabled, QLineEdit:disabled { background: %(disabled)s; color: %(disabled_text)s; border-color: %(line)s; }
    QPushButton[primary="true"] { background: %(primary)s; color: %(on_primary)s; min-height: 40px; }
    QPushButton[primary="true"]:hover { background: %(primary_hover)s; }
    QPushButton[primary="true"]:pressed { background: %(primary_pressed)s; }
    QPushButton[primary="true"]:disabled { background: %(disabled)s; color: %(disabled_text)s; border-color: %(line)s; }
    QPushButton[nav="true"] { text-align: left; background: transparent; border: 1px solid transparent; border-left: 3px solid transparent; min-height: 38px; padding: 0 9px; }
    QPushButton[nav="true"]:hover { background: %(hover)s; }
    QPushButton[nav="true"]:checked { background: %(selected)s; color: %(selected_text)s; border-left-color: %(primary)s; }
    QPushButton[nav="true"]:focus { border: 1px solid transparent; border-left: 3px solid transparent; }
    QPushButton[nav="true"]:focus:unchecked { border-top-color: %(focus)s; border-right-color: %(focus)s; border-bottom-color: %(focus)s; }
    QPushButton[nav="true"]:checked:focus { background: %(selected)s; color: %(selected_text)s; border: 1px solid transparent; border-left: 3px solid %(primary)s; }
    QPushButton#watcherStatus { text-align: left; min-height: 34px; padding: 0 10px; }
    QPushButton#watcherStatus[watcherState="on"] { background: %(success_bg)s; color: %(success)s; border-color: %(success)s; }
    QPushButton#watcherStatus[watcherState="paused"] { background: %(warning_bg)s; color: %(warning)s; border-color: %(warning)s; }
    QPlainTextEdit { background: %(surface)s; border: 1px solid %(border)s; border-radius: 8px; padding: 16px; font-family: Consolas; font-size: 14px; selection-background-color: %(selection)s; selection-color: %(selection_text)s; }
    QPlainTextEdit:hover { border-color: %(border_hover)s; }
    QPlainTextEdit:focus { border: 2px solid %(focus)s; background: %(focus_surface)s; padding: 15px; }
    QPlainTextEdit[validation="error"] { border: 2px solid %(error)s; padding: 15px; }
    QPlainTextEdit[validation="error"]:focus { border: 2px solid %(focus)s; background: %(focus_surface)s; }
    QComboBox { padding-right: 38px; }
    QComboBox::drop-down { border: none; width: 32px; }
    QComboBox QAbstractItemView { background: %(surface)s; color: %(text)s; border: 2px solid %(focus)s;
        outline: none; padding: 4px; selection-background-color: %(selected)s; selection-color: %(selected_text)s; }
    QComboBox QAbstractItemView::item { min-height: 36px; padding: 0 10px; }
    QCheckBox, QRadioButton { spacing: 10px; min-height: 32px; background: transparent; padding: 2px 4px; }
    QCheckBox::indicator, QRadioButton::indicator { width: 18px; height: 18px; }
    QCheckBox::indicator { background: %(surface)s; border: 2px solid %(border)s; border-radius: 3px; }
    QCheckBox::indicator:hover { border-color: %(border_hover)s; background: %(hover)s; }
    QCheckBox::indicator:checked { background: %(primary)s; border-color: %(primary)s; }
    QCheckBox::indicator:checked:hover { background: %(primary_hover)s; border-color: %(primary_hover)s; }
    QCheckBox::indicator:disabled { background: %(disabled)s; border-color: %(line)s; }
    QCheckBox:focus, QRadioButton:focus { background: %(focus_surface)s; border: 1px solid %(focus)s; border-radius: 4px; }
    QScrollArea { background: transparent; border: none; }
    QScrollArea > QWidget > QWidget { background: %(bg)s; }
    QScrollBar:vertical { width: 12px; background: transparent; margin: 0; }
    QScrollBar::handle:vertical { background: %(border)s; min-height: 32px; border-radius: 5px; margin: 2px; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
    QHeaderView::section { background: %(recessed)s; color: %(secondary)s; border: none; padding: 12px; font-size: 12px; font-weight: 600; }
    QTableWidget { background: %(surface)s; border: none; gridline-color: %(line)s; }
    QTabWidget::pane { background: %(surface)s; border: 1px solid %(line)s; border-radius: 8px; top: -1px; }
    QTabBar::tab { background: transparent; color: %(secondary)s; padding: 10px 16px; border-bottom: 2px solid transparent; }
    QTabBar::tab:hover { background: %(hover)s; }
    QTabBar::tab:selected { color: %(text)s; font-weight: 600; border-bottom-color: %(primary)s; }
    QListWidget { background: %(surface)s; border: 1px solid %(line)s; border-radius: 8px; padding: 8px; outline: none; }
    QListWidget::item { padding: 8px 12px; border-radius: 6px; border: 1px solid transparent; }
    QListWidget::item:hover { background: %(hover)s; }
    QListWidget::item:selected { background: %(selected)s; color: %(selected_text)s; border-left: 3px solid %(primary)s; }
    QListWidget::item:focus { border: 1px solid %(focus)s; }
    QListWidget:focus, QTreeWidget:focus, QTableWidget:focus { border: 2px solid %(focus)s; }
    QTreeWidget { background: %(surface)s; border: 1px solid %(line)s; border-radius: 8px; padding: 8px; outline: none; }
    QTreeWidget::item { border-left: 3px solid transparent; padding-left: 12px; }
    QTreeWidget::item:hover { background: %(hover)s; }
    QTreeWidget::item:selected { background: %(selected)s; color: %(selected_text)s; border-left-color: %(primary)s; }
    QProgressBar { background: %(line)s; border: none; border-radius: 2px; min-height: 4px; max-height: 4px; }
    QProgressBar::chunk { background: %(primary)s; border-radius: 2px; }
    QLabel[feedback="success"] { color: %(success)s; background: %(success_bg)s; border-left: 3px solid %(success)s; padding: 12px; }
    QLabel[feedback="warning"] { color: %(warning)s; background: %(warning_bg)s; border-left: 3px solid %(warning)s; padding: 12px; }
    QLabel[feedback="error"] { color: %(error)s; background: %(error_bg)s; border-left: 3px solid %(error)s; padding: 12px; }
    QDialog { background: %(bg)s; }
    QFrame#settingsSection { background: %(surface)s; border: 1px solid %(line)s; border-radius: 8px; }
    QMenu { background: %(surface)s; border: 1px solid %(line)s; padding: 8px; }
    QMenu::item { padding: 8px 20px; }
    QMenu::item:selected { background: %(selected)s; color: %(selected_text)s; }
    QMenu::item:disabled { color: %(disabled_text)s; }
    QToolTip { background: %(surface)s; color: %(text)s; border: 1px solid %(border)s; padding: 6px; font-size: 12px; }
    ''' % dict(c, family=family))
    return c
