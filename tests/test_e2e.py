"""
LIFTR end-to-end tests (Playwright + pytest).

Run:  pip install pytest playwright && playwright install chromium
      pytest tests/ -q
"""
import functools, http.server, os, socketserver, threading
import pytest
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIEWPORTS = {"iphone15": (393, 852), "iphone-se": (375, 667), "small": (320, 568), "landscape": (852, 393)}


@pytest.fixture(scope="session")
def base_url():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=ROOT)
    handler.log_message = lambda *a: None
    srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        exe = os.environ.get("CHROMIUM_PATH")
        b = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        yield b
        b.close()


@pytest.fixture(params=list(VIEWPORTS))
def page(request, browser, base_url):
    w, h = VIEWPORTS[request.param]
    ctx = browser.new_context(viewport={"width": w, "height": h}, has_touch=True)
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(base_url)
    pg.evaluate("localStorage.clear()")
    pg.reload()
    yield pg
    assert not errors, errors
    ctx.close()


def warmup(pg):
    return pg.inner_text("#wm-warmup")


def test_no_horizontal_scroll(page):
    for name in ("programa", "registro", "progreso", "nutricion"):
        page.evaluate(f"showPage('{name}')")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), name


def test_warmup_in_workout_mode(page):
    page.evaluate("currentDay = 1; openWorkoutMode()")
    assert "Escribe el peso" in warmup(page)
    page.fill("#wm-weight", "30")
    assert "15kg" in warmup(page) and "21kg" in warmup(page)       # 50% / 70%
    page.fill("#wm-weight", "22,5")                                   # comma decimals
    assert "11" in warmup(page)
    page.fill("#wm-weight", "30"); page.fill("#wm-reps", "12")
    page.evaluate("wmSerieHecha()")
    assert warmup(page).strip() == ""                                 # hidden after 1st set
    page.evaluate("wmUndoSet()")
    assert "15kg" in warmup(page)
    page.evaluate("wmLoadExercise(3)")                                # bodyweight dips
    assert warmup(page).strip() == ""


def test_sets_persist_and_progression(page):
    page.evaluate("currentDay = 1; openWorkoutMode()")
    for reps in (14, 12, 10):                                         # beat 12/10/8 by +2
        page.fill("#wm-weight", "30"); page.fill("#wm-reps", str(reps))
        page.evaluate("wmSerieHecha()")
    data = page.evaluate("JSON.parse(localStorage.getItem('liftr_v3'))")
    entry = data["w1"]["d1_e2"]
    assert [s["reps"] for s in entry["sets"]] == [14, 12, 10]
    assert entry["rec"]["action"] in ("up", "up_big")
    assert entry["rec"]["nextWeight"] > 30


def test_rest_timer_bar_does_not_cover_toast(page):
    page.evaluate("showPage('registro'); startTimer(90)")
    page.wait_for_timeout(200)
    page.evaluate("showToast('Introduce las reps')")
    page.wait_for_timeout(450)
    r = page.evaluate("""(() => {
      const ov = document.getElementById('timer-overlay').getBoundingClientRect();
      const t = document.getElementById('toast').getBoundingClientRect();
      const btns = [...document.querySelectorAll('#timer-overlay button')].map(b => b.getBoundingClientRect());
      return { above: t.bottom <= ov.top, inView: btns.every(b => b.left >= 0 && b.right <= innerWidth && b.bottom <= innerHeight) };
    })()""")
    assert r["above"] and r["inView"]


def test_export_import_roundtrip(page):
    page.evaluate("currentDay = 1; openWorkoutMode()")
    page.fill("#wm-weight", "40"); page.fill("#wm-reps", "12"); page.evaluate("wmSerieHecha()")
    backup = page.evaluate("JSON.stringify({app:'LIFTR', version:2, week:1, day:1, data: JSON.parse(localStorage.getItem('liftr_v3')), bodyweight: []})")
    restored = page.evaluate(f"JSON.stringify(validateBackup({backup}))")
    assert restored and '"reps":12' in restored and '"weight":40' in restored
    assert page.evaluate("validateBackup({foo: 'bar'})") in (None, False) or '"reps"' not in page.evaluate("JSON.stringify(validateBackup({foo:'bar'}))")


def test_pwa_assets(page, base_url):
    for path in ("manifest.webmanifest", "sw.js", "favicon.svg", "favicon.ico",
                 "icons/icon-192.png", "icons/icon-512.png", "icons/icon-maskable-512.png",
                 "icons/apple-touch-icon.png", "og-image.png", "robots.txt", "sitemap.xml"):
        assert page.request.get(base_url + path).status == 200, path
    manifest = page.request.get(base_url + "manifest.webmanifest").json()
    assert manifest["display"] == "standalone"
    assert any(i.get("purpose") == "maskable" for i in manifest["icons"])
