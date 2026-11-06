# Προετοιμασία κανονικού διακομιστή

Η βασική τοπική χρήση παραμένει `py server.py`, χωρίς επιπλέον βιβλιοθήκες. Το παρακάτω είναι προετοιμασία για χειριστή συστήματος. Δεν δημιουργεί φιλοξενία, domain, πιστοποιητικό HTTPS ή λογαριασμό παρόχου. Δεν εγκρίνει δημόσια κυκλοφορία.

## Προαιρετικός διακομιστής Waitress

Χρησιμοποίησε Python 3.12+ και ξεχωριστό virtual environment έξω από τον φάκελο κώδικα. Από τη ρίζα του κώδικα, σε Linux/macOS:

```sh
python3 -m venv ../academy-production-venv
../academy-production-venv/bin/python -m pip install --require-hashes --no-deps -r requirements-runtime.txt
```

Σε Windows CMD, από τον ίδιο φάκελο:

```bat
py -m venv ..\academy-production-venv
..\academy-production-venv\Scripts\python.exe -m pip install --require-hashes --no-deps -r requirements-runtime.txt
```

Το `waitress==3.0.2` και το SHA256 του wheel επαληθεύτηκαν από το επίσημο PyPI. Η εφαρμογή WSGI χρησιμοποιεί τις ίδιες υπηρεσίες και ελέγχους με τον τοπικό server, χωρίς ενδιάμεση σύνδεση HTTP ή κρυφή θύρα.

Ο χειριστής ορίζει στο ασφαλές περιβάλλον:

| Μεταβλητή | Απαίτηση |
| --- | --- |
| `ACADEMY_ENV` | `production` |
| `ACADEMY_APP_ORIGIN` | Το πραγματικό HTTPS origin, χωρίς path/query/fragment ή στοιχεία σύνδεσης |
| `ACADEMY_SECURE_COOKIES` | `1` |
| `ACADEMY_DB` | Ρητή απόλυτη διαδρομή ιδιωτικής βάσης έξω από το checkout |
| `HOST`, `PORT` | Προεπιλογή `127.0.0.1:8000` για ιδιωτική σύνδεση με reverse proxy |
| `ACADEMY_TRUSTED_PROXY` | Προαιρετικά η ακριβής IP ενός έμπιστου proxy· απαγορεύεται wildcard/CIDR |

Μετά τη ρύθμιση, εκκίνηση από τη ρίζα με `../academy-production-venv/bin/python run-production.py` σε Linux/macOS ή `..\academy-production-venv\Scripts\python.exe run-production.py` σε Windows CMD. Χρησιμοποίησε αυτή την είσοδο ώστε να εφαρμοστούν τα όρια και οι έλεγχοι proxy. Η παλιά εντολή `python server.py` αρνείται να ξεκινήσει με `ACADEMY_ENV=production`.

Ο Waitress δεν τερματίζει μόνος του HTTPS. Ο επιλεγμένος reverse proxy χρειάζεται έγκυρο πιστοποιητικό, να διατηρεί το εξωτερικό **Host** και να αντικαθιστά σωστά τα στοιχεία προώθησης πελάτη. Μόνο η ρητά έμπιστη IP μπορεί να δώσει `X-Forwarded-For` και `X-Forwarded-Proto`, για μία ενδιάμεση διαδρομή. Το `X-Forwarded-Host` δεν καθορίζει την ταυτότητα της εφαρμογής. Κράτησε τη θύρα εφαρμογής ιδιωτική. Χωρίς ρύθμιση έμπιστου proxy τα forwarded headers αγνοούνται και τα όρια IP πίσω από proxy θα είναι κοινά για τους χρήστες.

Η προετοιμασία αυτή αφορά μικρή εγκατάσταση SQLite/ενός process. Δεν τεκμηριώνει χωρητικότητα για χιλιάδες χρήστες ή υψηλή διαθεσιμότητα.

## Email λογαριασμών

Η προεπιλογή `ACADEMY_MAIL_MODE=disabled` παραμένει λειτουργική για μαθήματα, αλλά δεν προσποιείται αποστολή συνδέσμων. Για πραγματική αποστολή χρειάζονται έγκριση, πραγματικός πάροχος και ασφαλής ρύθμιση:

| Μεταβλητή | Περιεχόμενο |
| --- | --- |
| `ACADEMY_MAIL_MODE` | `smtp` |
| `ACADEMY_SMTP_HOST` | SMTP hostname του παρόχου, χωρίς URL |
| `ACADEMY_SMTP_PORT` | `465` για implicit TLS ή `587` για STARTTLS |
| `ACADEMY_SMTP_USER` | Πραγματικό username SMTP |
| `ACADEMY_SMTP_PASSWORD` | Πραγματικό password SMTP, σε ασφαλές περιβάλλον |
| `ACADEMY_SMTP_FROM` | Εξουσιοδοτημένη διεύθυνση αποστολέα |
| `ACADEMY_APP_ORIGIN` | Το πραγματικό origin των συνδέσμων |

Ποτέ μην καταχωρίζεις κωδικούς στο chat, στο Git ή στα αρχεία οδηγιών. Το `.env.example` είναι τεκμηρίωση· η εφαρμογή δεν φορτώνει αυτόματα `.env`. Τα proxy placeholders HTTPS δεν είναι ανεξάρτητα raw SMTP credentials. Στο παρόν cloud δεν έχει επαληθευθεί εξωτερική πρόσβαση SMTP/TCP ή κάποια σύνδεση παρόχου.

Το TLS επαληθεύει τα πιστοποιητικά. Η εφαρμογή δεν συνδέεται χωρίς TLS και δεν απενεργοποιεί τον έλεγχο CA. Επιβεβαίωσε με τον πάροχο DNS/DKIM/SPF/DMARC, επιτρεπόμενο αποστολέα, quota, ανεπιθύμητα και πραγματική παράδοση πριν από χρήση.

Υλοποιήθηκαν επιβεβαίωση διεύθυνσης, ανάκτηση κωδικού και αλλαγή διεύθυνσης μετά από επιβεβαίωση. Τα tokens αποθηκεύονται μόνο ως hashes, έχουν σκοπό/λήξη/μοναδική χρήση και δεν εμφανίζονται στα HTTP paths: οι σύνδεσμοι χρησιμοποιούν fragment του browser. Η αλλαγή κωδικού ή διεύθυνσης ανακαλεί τις συνεδρίες και δεν κάνει αυτόματο login. Οι παλιοί μη επιβεβαιωμένοι λογαριασμοί δεν αποκλείονται από τα μαθήματα.

Η ουρά email είναι περιορισμένη σε 100 εργασίες **στη μνήμη** και κάνει μία προσπάθεια παράδοσης. Αποδοχή αιτήματος δεν εγγυάται παράδοση. Επανεκκίνηση μπορεί να χάσει εκκρεμή email· δεν υπάρχει μόνιμο outbox ή αυτόματη επανάληψη. Τα quotas προστασίας αποθηκεύουν αλατισμένα hashes, όχι raw IP/email identifiers. Για λειτουργία με εγγυήσεις χρειάζεται χωριστό σχέδιο παράδοσης/παρακολούθησης.

## Διάγνωση πριν από εκκίνηση

```sh
../academy-production-venv/bin/python manage.py doctor
../academy-production-venv/bin/python manage.py doctor --production --json
```

Σε Windows χρησιμοποίησε αντίστοιχα το `..\academy-production-venv\Scripts\python.exe`.

Ο έλεγχος διαβάζει ρυθμίσεις και ακεραιότητα υπάρχουσας βάσης. Δεν στέλνει email, δεν ανοίγει server, δεν δημιουργεί βάση ή υπηρεσία και δεν εμφανίζει διαπιστευτήρια. Το production check ελέγχει και την προαιρετική βιβλιοθήκη/SMTP. Επιτυχία σημαίνει μόνο ότι πέρασαν οι προϋποθέσεις που ελέγχθηκαν. Δες το [RELEASE_CHECKLIST.md](../RELEASE_CHECKLIST.md) για τα μη επαληθευμένα σημεία.

## Δοκιμές που εκτελούνται τοπικά

Η πλήρης unittest συλλογή καλύπτει WSGI και ροές email με δοκιμαστικό παραλήπτη στη μνήμη και mocked SMTP. Το `tests/browser_email.py` ελέγχει τις οθόνες. Το `tests/production_smoke.py`, με εγκατεστημένο Waitress, εκκινεί πραγματικές ιδιωτικές διεργασίες και προσομοιώνει headers reverse proxy. Δεν παρέχει πραγματικό TLS certificate ή απόδειξη παράδοσης από SMTP πάροχο.
