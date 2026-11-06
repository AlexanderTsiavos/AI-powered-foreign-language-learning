"""Offline prerequisite checks; never a claim of public release approval."""
import importlib.metadata
import os
from pathlib import Path
import sqlite3
import sys


def diagnose(*, database=None, production=False, environ=None):
    env = dict(os.environ if environ is None else environ)
    checks = []

    def add(name, passed, message, optional=False):
        checks.append({'name': name, 'status': 'optional' if optional else 'passed' if passed else 'failed', 'message': message})

    add('python', sys.version_info >= (3, 12), 'Απαιτείται Python 3.12 ή νεότερη.')
    from manage import check_database, default_database, ManagementError
    db_path = Path(database or env.get('ACADEMY_DB') or default_database()).expanduser().resolve()
    try:
        check_database(db_path)
        add('database', True, 'Η υπάρχουσα βάση πέρασε τον έλεγχο ακεραιότητας. Δεν τροποποιήθηκε.')
    except (ManagementError, sqlite3.Error, OSError):
        add('database', False, 'Δεν βρέθηκε έγκυρη υπάρχουσα βάση. Εκκίνησε πρώτα την εφαρμογή ή έλεγξε το ACADEMY_DB.')
    from mailer import MailSettings
    settings = MailSettings.from_env(env)
    if settings.mode == 'disabled':
        add('mail', not production, 'Η αποστολή email είναι απενεργοποιημένη. Δεν έγινε σύνδεση ή αποστολή.', optional=not production)
    else:
        add('mail', settings.mode == 'smtp' and settings.configured,
            'Ελέγχθηκαν μόνο οι ρυθμίσεις SMTP/TLS. Η πραγματική παράδοση email χρειάζεται ξεχωριστή δοκιμή.')
    if production:
        from wsgi import ProductionConfigError, validate_production_config
        try:
            config = validate_production_config(env)
            add('https_configuration', True, 'Οι απαιτούμενες ρυθμίσεις HTTPS origin, Secure cookie και εξωτερικής βάσης είναι έγκυρες.')
            add('proxy', True, 'Έχει οριστεί συγκεκριμένο έμπιστο proxy.' if config.trusted_proxy else
                'Δεν εμπιστευόμαστε forwarded headers. Πίσω από proxy τα όρια IP θα είναι κοινά μέχρι να ρυθμιστεί ACADEMY_TRUSTED_PROXY.')
        except ProductionConfigError as error:
            add('https_configuration', False, str(error))
        try:
            version = importlib.metadata.version('waitress')
            add('runtime', version == '3.0.2', 'Χρειάζεται ο επαληθευμένος Waitress από το requirements-runtime.txt.')
        except importlib.metadata.PackageNotFoundError:
            add('runtime', False, 'Εγκατάστησε το προαιρετικό requirements-runtime.txt με --require-hashes.')
    return {'scope': 'production-prerequisites' if production else 'local-prerequisites',
            'checksPassed': all(check['status'] != 'failed' for check in checks), 'checks': checks,
            'unverified': ['Πραγματικό DNS, HTTPS και ρύθμιση reverse proxy.',
                           'Πραγματική παράδοση email από τον επιλεγμένο πάροχο.',
                           'Πραγματικό μοντέλο AI, μικρόφωνο και φυσικές συσκευές.',
                           'Πληρωμές, πλήρες εκπαιδευτικό πρόγραμμα και νομική έγκριση.'],
            'notice': 'Ο έλεγχος αφορά προϋποθέσεις. Δεν εγκρίνει δημόσια κυκλοφορία και δεν ενεργοποιεί υπηρεσίες.'}
