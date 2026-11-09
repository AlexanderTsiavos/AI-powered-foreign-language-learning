"""Derived study-plan UI checks using private temporary accounts and real records.

Run: python3 tests/browser_study_plan.py (optional Playwright/Chromium).
No mail, model, external analytics, measured study time or real device is used.
"""
import os
from pathlib import Path
import sys
import tempfile
import time
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import create_server
from playwright.sync_api import sync_playwright, expect
from browser_learning import mutate, session


def run():
    artifacts=Path('/tmp/academy-study-plan-artifacts')
    artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory, sync_playwright() as p:
        server=create_server('127.0.0.1',0,Path(directory)/'study.sqlite3')
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),args=['--no-sandbox'])
        context=browser.new_context(viewport={'width':1440,'height':1000})
        page=context.new_page()
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.on('console',lambda message:errors.append(message.text) if message.type=='error' and ('Content Security Policy' in message.text or 'Refused to' in message.text) else None)
        url=f'http://127.0.0.1:{server.server_port}'
        held=[]
        try:
            mutate(context,url,'POST','/api/register',{'name':'Ελένη Μελέτη','email':'study@example.test','password':'study unique password 42','ageConfirmed':True})
            mutate(context,url,'PUT','/api/profile',{'goalMinutes':10,'reason':'travel'})
            user_id=session(context,url)['user']['id']
            curriculum=context.request.get(url+'/api/curriculum').json()['lessons']
            page.goto(url+'/#progress')
            expect(page.locator('#study-plan-summary')).to_contain_text(f"0 από {len(curriculum)} μαθήματα")
            expect(page.locator('[data-study-unit]')).to_have_count((len(curriculum)+2)//3)
            page.locator('.study-activity-records > summary').click()
            expect(page.locator('#study-activity-table tbody tr')).to_have_count(14)
            assert all(value=='0' for value in page.locator('#study-activity-table tbody td').all_text_contents())
            assert page.locator('[data-study-unit=unit-1] a').count()==1
            expect(page.locator('.study-session')).to_contain_text('Δεν μετράμε πραγματικό χρόνο μελέτης')
            print('PASS empty honest progress, 14 Athens days, dynamic units and prerequisite links',flush=True)

            failed=False
            def fail_once(route):
                nonlocal failed
                if not failed:
                    failed=True
                    route.abort()
                else:
                    route.continue_()
            page.route('**/api/study-plan',fail_once)
            page.reload()
            expect(page.locator('[data-action=study-plan-retry]')).to_be_visible()
            page.locator('[data-action=study-plan-retry]').click()
            expect(page.locator('#study-plan-summary')).to_contain_text('0 από')
            page.unroute('**/api/study-plan',fail_once)
            print('PASS study-plan request failure and explicit recovery',flush=True)

            first=curriculum[0]
            page.goto(url+'/#lesson/'+first['id'])
            expect(page.locator('.whiteboard')).to_be_visible()
            for _ in first['cards']:
                page.locator('[data-action=next-card]').click()
            for exercise in first['exercises']:
                page.locator('#answer').fill(exercise['answer'])
                page.locator('#exercise-form button[type=submit]').click()
                expect(page.locator('#answer-feedback')).to_contain_text('Σωστά!')
                page.locator('#exercise-form button[type=submit]').click()
            expect(page.locator('.completion-page')).to_be_visible()
            page.goto(url+'/#progress')
            expect(page.locator('#study-plan-summary')).to_contain_text(f"1 από {len(curriculum)} μαθήματα")
            page.locator('.study-activity-records > summary').click()
            today=context.request.get(url+'/api/study-plan').json()['today']
            row=page.locator(f'[data-activity-date="{today}"]')
            expect(row.locator('td').nth(0)).to_have_text('1')
            expect(row.locator('td').nth(1)).to_have_text(str(first['xp']))
            expect(page.locator('[data-study-unit=unit-1] .count-pill')).to_have_text('1 / 3')
            page.screenshot(path=str(artifacts/'progress-desktop.png'),full_page=True)
            print('PASS real lesson completion updates daily activity, XP and unit completion',flush=True)

            second=curriculum[1]
            mutate(context,url,'PUT','/api/lesson-state',{'lessonId':second['id'],'phase':'cards','index':1,'answers':[],'correct':False})
            page.goto(url+'/#dashboard')
            expect(page.locator('#study-recommendation')).to_have_attribute('data-recommendation','lesson')
            expect(page.locator('#study-recommendation')).to_contain_text('αποθηκευμένη θέση')
            expect(page.locator('[data-map-unit]')).to_have_count((len(curriculum)+2)//3)
            expect(page.locator('.path-row.locked')).to_have_count(len(curriculum)-2)
            page.locator('#study-recommendation a').click()
            expect(page.locator('.word-presentation h2')).to_have_text(second['cards'][1]['word'])
            assert context.request.get(url+'/api/study-plan').json()['summary']['xp']==first['xp']
            print('PASS saved checkpoint recommendation resumes exact card without unlocking later lessons',flush=True)

            with server.db() as db:
                db.execute("UPDATE review_cards SET due_at='2020-01-01T00:00:00+00:00' WHERE user_id=?",(user_id,))
            page.goto(url+'/#dashboard')
            expect(page.locator('#study-recommendation')).to_have_attribute('data-recommendation','review')
            expect(page.locator('#study-recommendation a')).to_have_attribute('href','#review')
            page.goto(url+'/#settings')
            page.locator('#settings-form [name=goalMinutes]').select_option('15')
            page.locator('#settings-form button[type=submit]').click()
            expect(page.locator('#toast')).to_contain_text('αποθηκεύτηκε')
            page.goto(url+'/#progress')
            expect(page.locator('.study-session')).to_contain_text('Προσωπικός στόχος: 15 λεπτά')
            assert context.request.get(url+'/api/study-plan').json()['summary']['completedLessons']==1
            page.set_viewport_size({'width':320,'height':740})
            expect(page.locator('#study-plan-details')).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),'320px progress overflow'
            expect(page.locator('#toast')).to_be_hidden(timeout=7000)
            page.screenshot(path=str(artifacts/'progress-mobile.png'),full_page=True)
            page.goto(url+'/#dashboard')
            expect(page.locator('#study-recommendation')).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),'320px map overflow'
            page.screenshot(path=str(artifacts/'dashboard-mobile.png'),full_page=True)
            print('PASS due review takes priority, goal remains a preference, 320px progress and grouped map',flush=True)

            page.set_viewport_size({'width':1440,'height':1000})
            review_requests=[]
            def hold_review(route):
                if route.request.method=='POST' and not review_requests:
                    review_requests.append(route)
                else:
                    route.continue_()
            page.route('**/api/review',hold_review)
            page.goto(url+'/#review')
            expect(page.locator('#review-answer')).to_be_visible()
            first_due=next(item for item in context.request.get(url+'/api/review').json()['items'] if item['isDue'])
            page.locator('#review-answer').fill(first_due['answer'])
            page.locator('#review-form button[type=submit]').click()
            deadline=time.monotonic()+5
            while not review_requests and time.monotonic()<deadline:
                page.wait_for_timeout(20)
            assert review_requests
            page.goto(url+'/#progress')
            expect(page.locator('.study-session')).to_contain_text('3 στοιχεία')
            review_requests[0].continue_()
            expect(page.locator('.study-session')).to_contain_text('2 στοιχεία')
            page.unroute('**/api/review',hold_review)
            with server.db() as db:
                db.execute("UPDATE review_cards SET due_at='2099-01-01T00:00:00+00:00' WHERE user_id=?",(user_id,))
                db.execute('DELETE FROM lesson_checkpoints WHERE user_id=? AND lesson_id=?',(user_id,second['id']))
            checkpoint_requests=[]
            def hold_checkpoint(route):
                if route.request.method=='PUT' and not checkpoint_requests:
                    checkpoint_requests.append(route)
                else:
                    route.continue_()
            page.route('**/api/lesson-state',hold_checkpoint)
            page.goto(url+'/#lesson/'+second['id'])
            expect(page.locator('.word-presentation h2')).to_have_text(second['cards'][0]['word'])
            page.locator('[data-action=next-card]').click()
            deadline=time.monotonic()+5
            while not checkpoint_requests and time.monotonic()<deadline:
                page.wait_for_timeout(20)
            assert checkpoint_requests
            page.goto(url+'/#dashboard')
            expect(page.locator('#study-recommendation')).to_be_visible()
            expect(page.locator('#study-recommendation')).not_to_contain_text('αποθηκευμένη θέση')
            checkpoint_requests[0].continue_()
            expect(page.locator('#study-recommendation')).to_contain_text('αποθηκευμένη θέση')
            page.unroute('**/api/lesson-state',hold_checkpoint)
            print('PASS late review/checkpoint confirmations refresh the currently open plan after navigation',flush=True)
            # Hold an already authorized first-account response across logout and
            # a second login, then release it into the same JavaScript document.
            def hold_first(route):
                if not held:
                    response=route.fetch()
                    assert response.ok
                    held.append((route,response.json()))
                else:
                    route.continue_()
            page.route('**/api/study-plan',hold_first)
            page.goto(url+'/#progress',wait_until='domcontentloaded')
            expect(page.locator('.study-loading')).to_be_visible()
            deadline=time.monotonic()+5
            while not held and time.monotonic()<deadline:
                page.wait_for_timeout(20)
            assert held
            page.locator('[data-action=logout]').first.click()
            expect(page.locator('.hero')).to_be_visible()
            page.get_by_role('link',name='Ας κάνουμε την αρχή').click()
            page.locator('[name=name]').fill('Νέος Μαθητής')
            page.locator('[name=email]').fill('study-second@example.test')
            page.locator('[name=password]').fill('second study password 42')
            page.locator('[name=ageConfirmed]').check()
            page.locator('#auth-form button[type=submit]').click()
            expect(page.locator('#onboarding-form')).to_be_visible()
            page.locator('#onboarding-form button[type=submit]').click()
            expect(page.locator('.dashboard-page')).to_be_visible()
            page.get_by_role('link',name='Η πρόοδός μου',exact=True).first.click()
            expect(page.locator('#study-plan-summary')).to_contain_text(f"0 από {len(curriculum)} μαθήματα")
            held[0][0].fulfill(status=200,json=held[0][1])
            expect(page.locator('#study-plan-summary')).to_contain_text(f"0 από {len(curriculum)} μαθήματα")
            page.locator('.study-activity-records > summary').click()
            assert all(value=='0' for value in page.locator('#study-activity-table tbody td').all_text_contents())
            assert not errors,errors
            print('PASS late private response cannot replace another account progress; no JavaScript runtime errors',flush=True)
            print('Screenshots: '+str(artifacts),flush=True)
        finally:
            browser.close()
            server.shutdown()
            server.server_close()
            thread.join()


if __name__=='__main__':
    run()
