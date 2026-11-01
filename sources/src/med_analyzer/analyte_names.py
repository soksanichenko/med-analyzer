"""Display names of the seed analytes in English and Russian.

The database holds the Ukrainian name (what the labs print); the other two
languages are looked up here by the analyte ``code``. An analyte that is not
listed (none today) is shown under its stored name.
"""

# code -> (English, Russian)
ANALYTE_NAMES: dict[str, tuple[str, str]] = {
    'WBC': ('White blood cells (leukocytes)', 'Лейкоциты'),
    'RBC': ('Red blood cells (erythrocytes)', 'Эритроциты'),
    'HGB': ('Hemoglobin', 'Гемоглобин'),
    'HCT': ('Hematocrit', 'Гематокрит'),
    'MCV': ('Mean corpuscular volume', 'Средний объём эритроцита'),
    'MCH': (
        'Mean corpuscular hemoglobin',
        'Среднее содержание гемоглобина в эритроците',
    ),
    'MCHC': (
        'Mean corpuscular hemoglobin concentration',
        'Средняя концентрация гемоглобина в эритроците',
    ),
    'PLT': ('Platelets', 'Тромбоциты'),
    'RDW-SD': (
        'Red cell distribution width (standard deviation)',
        'Ширина распределения эритроцитов (стандартное отклонение)',
    ),
    'RDW-CV': (
        'Red cell distribution width (coefficient of variation)',
        'Ширина распределения эритроцитов (коэффициент вариации)',
    ),
    'PDW': ('Platelet distribution width', 'Ширина распределения тромбоцитов'),
    'MPV': ('Mean platelet volume', 'Средний объём тромбоцитов'),
    'PCT': ('Plateletcrit', 'Тромбокрит'),
    'P-LCR': (
        'Platelet large cell ratio',
        'Отношение крупных тромбоцитов к общему количеству тромбоцитов',
    ),
    'NEUT%': ('Neutrophils, %', 'Нейтрофилы, %'),
    'LYM%': ('Lymphocytes, %', 'Лимфоциты, %'),
    'MONO%': ('Monocytes, %', 'Моноциты, %'),
    'EO%': ('Eosinophils, %', 'Эозинофилы, %'),
    'BASO%': ('Basophils, %', 'Базофилы, %'),
    'IMG%': ('Immature granulocytes, %', 'Незрелые гранулоциты, %'),
    'NEUT#': ('Neutrophils, absolute', 'Нейтрофилы, абс.'),
    'LYM#': ('Lymphocytes, absolute', 'Лимфоциты, абс.'),
    'MONO#': ('Monocytes, absolute', 'Моноциты, абс.'),
    'EO#': ('Eosinophils, absolute', 'Эозинофилы, абс.'),
    'BASO#': ('Basophils, absolute', 'Базофилы, абс.'),
    'IMG#': ('Immature granulocytes, absolute', 'Незрелые гранулоциты, абс.'),
    'ESR': (
        'Erythrocyte sedimentation rate (ESR)',
        'Скорость оседания эритроцитов (СОЭ)',
    ),
    'CREA': ('Creatinine', 'Креатинин'),
    'GLU': ('Glucose', 'Глюкоза'),
    'B12': ('Vitamin B12', 'Витамин B12'),
    'FERR': ('Ferritin', 'Ферритин'),
    'FOLATE': ('Folic acid (folate)', 'Фолиевая кислота'),
    'VITD': (
        'Vitamin D (25-hydroxyvitamin D)',
        'Витамин D (25-гидроксивитамин D)',
    ),
    'CHOL': ('Cholesterol', 'Холестерин'),
    'TRIG': ('Triglycerides', 'Триглицериды'),
    'HDL': (
        'High-density lipoprotein (HDL)',
        'Липопротеины высокой плотности (ЛПВП)',
    ),
    'LDL': (
        'Low-density lipoprotein (LDL)',
        'Липопротеины низкой плотности (ЛПНП)',
    ),
    'VLDL': (
        'Very-low-density lipoprotein (VLDL)',
        'Липопротеины очень низкой плотности (ЛПОНП)',
    ),
    'NONHDL': ('Non-HDL cholesterol', 'Холестерин не-ЛПВП'),
    'AI': ('Atherogenic index', 'Коэффициент атерогенности'),
    'TSH': ('Thyroid-stimulating hormone (TSH)', 'Тиреотропный гормон (ТТГ)'),
    'FT4': ('Free thyroxine', 'Тироксин свободный'),
    'FT3': ('Free triiodothyronine', 'Трийодтиронин свободный'),
    'AST': ('Aspartate aminotransferase (AST)', 'Аспартатаминотрансфераза (АСТ)'),
    'ALT': ('Alanine aminotransferase (ALT)', 'Аланинаминотрансфераза (АЛТ)'),
    'TPO-AB': ('Anti-thyroid peroxidase antibodies', 'Антитела к тиреопероксидазе'),
}


def analyte_name(code: str, stored_name: str, lang: str) -> str:
    """Return the display name of an analyte in a language.

    Args:
        code: Analyte code as stored.
        stored_name: The Ukrainian name from the database.
        lang: ``uk``, ``en`` or ``ru``.

    Returns:
        The name; the stored one for Ukrainian and for an unlisted code.
    """
    names = ANALYTE_NAMES.get(code)
    if names is None or lang == 'uk':
        return stored_name
    return names[0] if lang == 'en' else names[1]
