"""Small, dependency-free translation layer for displayed application text."""
from PySide6.QtCore import Qt


SUPPORTED_LANGUAGES = ('ar', 'en')
DEFAULT_LANGUAGE = 'ar'
_active_language = 'en'

_TEXT = {
    'en': {
        'nav.create': 'Create exam', 'nav.folder': 'Folder scan', 'nav.history': 'History',
        'nav.templates': 'Templates', 'nav.ai_generation': 'AI generation',
        'nav.ai_studio': 'AI Studio Automation', 'common.settings': 'Settings',
        'clipboard.watcher': 'Clipboard watcher', 'watcher.off': 'Off',
        'watcher.watching': 'Watching', 'watcher.paused': 'Paused',
        'common.navigate': 'Navigate', 'settings.title': 'Settings',
        'settings.interface_language': 'Interface language', 'language.ar': 'Arabic',
        'language.en': 'English', 'common.done': 'Done', 'common.choose_folder': 'Choose folder',
        'settings.files': 'Files', 'settings.appearance': 'Appearance and notifications',
        'settings.output_folder': 'Output folder', 'settings.default_template': 'Default template',
        'settings.theme': 'Theme', 'settings.notifications': 'Notifications',
        'common.open': 'Open', 'common.copy_path': 'Copy path', 'common.reveal_folder': 'Reveal in folder',
        'common.refresh': 'Refresh', 'common.import': 'Import template', 'common.remove': 'Remove',
        'history.export': 'Export CSV', 'history.open_exam': 'Open exam', 'history.remove': 'Remove from history',
        'folder.choose': 'Choose folder', 'folder.open_output': 'Open output folder', 'folder.start': 'Start scan',
        'page.create': 'Create exam', 'page.folder': 'Folder scan', 'page.history': 'History',
        'page.templates': 'Templates', 'page.ai_generation': 'AI generation',
        'page.ai_studio': 'AI Studio Automation',
        'language.restart_note': 'Interface language changed. Reopen Settings to see every label updated.',
        'create.import': 'Import file', 'create.questions': 'Questions', 'create.clear': 'Clear',
        'create.example': 'Try an example', 'create.paste': 'Paste from clipboard',
        'create.placeholder': 'Paste your quiz JSON here.', 'create.import_hint': 'You can also import a .json, .txt, or .md file.',
        'create.save_details': 'Save details', 'create.template': 'Template',
        'create.output_folder': 'Output folder', 'create.open_output': 'Open output folder',
        'create.quiz_title': 'Quiz title', 'create.waiting': 'Waiting for questions',
        'create.filename': 'Proposed filename', 'create.after_validation': 'Appears after validation',
        'create.generate': 'Generate exam', 'create.begin': 'Paste questions to begin.',
        'create.private_note': 'Your questions stay on this device until you generate an exam.',
        'create.no_template': 'No template selected',
    },
    'ar': {
        'nav.create': 'إنشاء اختبار', 'nav.folder': 'فحص المجلد', 'nav.history': 'السجل',
        'nav.templates': 'القوالب', 'nav.ai_generation': 'توليد بالذكاء الاصطناعي',
        'nav.ai_studio': 'أتمتة AI Studio', 'common.settings': 'الإعدادات',
        'clipboard.watcher': 'Clipboard Watcher', 'watcher.off': 'متوقف',
        'watcher.watching': 'قيد المراقبة', 'watcher.paused': 'متوقف مؤقتًا',
        'common.navigate': 'تنقّل', 'settings.title': 'الإعدادات',
        'settings.interface_language': 'لغة الواجهة', 'language.ar': 'العربية',
        'language.en': 'English', 'common.done': 'تم', 'common.choose_folder': 'اختر مجلدًا',
        'settings.files': 'الملفات', 'settings.appearance': 'المظهر والإشعارات',
        'settings.output_folder': 'مجلد الإخراج', 'settings.default_template': 'القالب الافتراضي',
        'settings.theme': 'المظهر', 'settings.notifications': 'الإشعارات',
        'common.open': 'فتح', 'common.copy_path': 'نسخ المسار', 'common.reveal_folder': 'إظهار في المجلد',
        'common.refresh': 'تحديث', 'common.import': 'استيراد قالب', 'common.remove': 'إزالة',
        'history.export': 'تصدير CSV', 'history.open_exam': 'فتح الامتحان', 'history.remove': 'إزالة من السجل',
        'folder.choose': 'اختر مجلدًا', 'folder.open_output': 'فتح مجلد الإخراج', 'folder.start': 'بدء الفحص',
        'page.create': 'إنشاء اختبار', 'page.folder': 'فحص المجلد', 'page.history': 'السجل',
        'page.templates': 'القوالب', 'page.ai_generation': 'توليد بالذكاء الاصطناعي',
        'page.ai_studio': 'أتمتة AI Studio',
        'language.restart_note': 'تم تغيير لغة الواجهة. أعد فتح الإعدادات لتحديث جميع التسميات.',
        'create.import': 'استيراد ملف', 'create.questions': 'الأسئلة', 'create.clear': 'مسح',
        'create.example': 'تجربة مثال', 'create.paste': 'لصق من الحافظة',
        'create.placeholder': 'الصق JSON الخاص بالأسئلة هنا.', 'create.import_hint': 'يمكنك أيضًا استيراد ملف .json أو .txt أو .md.',
        'create.save_details': 'تفاصيل الحفظ', 'create.template': 'القالب',
        'create.output_folder': 'مجلد الإخراج', 'create.open_output': 'فتح مجلد الإخراج',
        'create.quiz_title': 'عنوان الامتحان', 'create.waiting': 'بانتظار الأسئلة',
        'create.filename': 'اسم الملف المقترح', 'create.after_validation': 'يظهر بعد التحقق',
        'create.generate': 'إنشاء الامتحان', 'create.begin': 'الصق الأسئلة للبدء.',
        'create.private_note': 'تبقى أسئلتك على هذا الجهاز حتى إنشاء الامتحان.',
        'create.no_template': 'لم يتم اختيار قالب',
    },
}


def active_language():
    return _active_language


def set_language(language):
    """Select a supported display language and return the normalized code."""
    global _active_language
    _active_language = language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE
    return _active_language


def tr(key, **values):
    """Return translated display text, falling back to English then the key."""
    text = _TEXT.get(_active_language, {}).get(key, _TEXT['en'].get(key, key))
    return text.format(**values) if values else text


def apply_language(application, language):
    language = set_language(language)
    application.setLayoutDirection(Qt.RightToLeft if language == 'ar' else Qt.LeftToRight)
    return language
