#!/usr/bin/env python3
"""Kostka Client - prosty launcher Minecraft: logowanie Microsoft, Fabric, mody z Modrinth.
Wymaga tylko Pythona 3.9+ (z tkinter) - Javę 21 launcher pobiera sam. Zero dodatkowych bibliotek."""
import json, os, platform, re, shutil, subprocess, sys, threading, time
import urllib.error, urllib.parse, urllib.request, webbrowser
from concurrent.futures import ThreadPoolExecutor
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

CLIENT_ID = "00000000402b5328"  # jesli logowanie przestanie dzialac, wstaw wlasne ID z Azure
SCOPE = "service::user.auth.xboxlive.com::MBI_SSL"
LIVE = "https://login.live.com/oauth20_%s.srf"
ROOT = os.path.join(os.path.expanduser("~"), ".kostka")
CFG = os.path.join(ROOT, "config.json")
OS = {"win32": "windows", "darwin": "osx"}.get(sys.platform, "linux")
_m = platform.machine().lower()
ARCH = "arm64" if _m in ("arm64", "aarch64") else "x86" if _m in ("i386", "i686", "x86") else "x86_64"
BG, PN, AC, TX, DM = "#12161C", "#1B222B", "#2EE59D", "#E6EDF3", "#8B98A5"


# ---------- sieć i pliki ----------
def http(url, form=None, body=None, headers=None):
    h = {"User-Agent": "KostkaClient/1.0", "Accept": "application/json", **(headers or {})}
    data = None
    if form is not None: data = urllib.parse.urlencode(form).encode()
    elif body is not None: data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data, h), timeout=30) as r: raw = r.read()
    except urllib.error.HTTPError as e: raw = e.read()
    return json.loads(raw or b"{}")


def get(url, path):
    if os.path.exists(path): return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "KostkaClient/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r, open(path + ".part", "wb") as f: shutil.copyfileobj(r, f)
    os.replace(path + ".part", path)


def load():
    try:
        with open(CFG) as f: return json.load(f)
    except Exception: return {}


def save(c):
    os.makedirs(ROOT, exist_ok=True)
    with open(CFG, "w") as f: json.dump(c, f)


# ---------- logowanie Microsoft -> Xbox -> Minecraft ----------
def xbox(ms_token):
    x = http("https://user.auth.xboxlive.com/user/authenticate", body={"Properties": {"AuthMethod": "RPS",
             "SiteName": "user.auth.xboxlive.com", "RpsTicket": "t=" + ms_token},
             "RelyingParty": "http://auth.xboxlive.com", "TokenType": "JWT"})
    s = http("https://xsts.auth.xboxlive.com/xsts/authorize", body={"Properties": {"SandboxId": "RETAIL",
             "UserTokens": [x["Token"]]}, "RelyingParty": "rp://api.minecraftservices.com/", "TokenType": "JWT"})
    if "Token" not in s: raise RuntimeError(f"Xbox odmówił logowania (kod {s.get('XErr')}). Czy konto ma Xbox/Minecraft?")
    m = http("https://api.minecraftservices.com/authentication/login_with_xbox",
             body={"identityToken": f"XBL3.0 x={s['DisplayClaims']['xui'][0]['uhs']};{s['Token']}"})
    p = http("https://api.minecraftservices.com/minecraft/profile", headers={"Authorization": "Bearer " + m.get("access_token", "")})
    if "id" not in p: raise RuntimeError("To konto nie ma Minecraft Java Edition.")
    return {"token": m["access_token"], "uuid": p["id"], "name": p["name"]}


def login(say, cfg):
    tok = None
    if cfg.get("refresh"):
        t = http(LIVE % "token", form={"client_id": CLIENT_ID, "refresh_token": cfg["refresh"],
                 "grant_type": "refresh_token", "scope": SCOPE})
        tok = t if "access_token" in t else None
    if not tok:
        d = http(LIVE % "connect", form={"client_id": CLIENT_ID, "scope": SCOPE, "response_type": "device_code"})
        if "user_code" not in d: raise RuntimeError(d.get("error_description", "błąd logowania"))
        say(f"Wpisz kod  {d['user_code']}  na stronie {d['verification_uri']}  (otwieram przeglądarkę)")
        webbrowser.open(d["verification_uri"])
        end = time.time() + d["expires_in"]
        while not tok and time.time() < end:
            time.sleep(d.get("interval", 5))
            t = http(LIVE % "token", form={"client_id": CLIENT_ID, "device_code": d["device_code"],
                     "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
            if "access_token" in t: tok = t
            elif t.get("error") != "authorization_pending": raise RuntimeError(t.get("error_description", "logowanie nieudane"))
        if not tok: raise RuntimeError("kod wygasł, spróbuj ponownie")
    say("Logowanie do Xbox / Minecraft…")
    acc = xbox(tok["access_token"])
    cfg["refresh"] = tok["refresh_token"]; save(cfg)
    return acc


# ---------- przygotowanie i start gry ----------
def ok(rules):
    if not rules: return True
    res = False
    for r in rules:
        o = r.get("os", {})
        if "features" not in r and o.get("name", OS) == OS and o.get("arch", ARCH) == ARCH:
            res = r["action"] == "allow"
    return res


def flat(args):
    for a in args:
        if isinstance(a, str): yield a
        elif ok(a.get("rules")):
            v = a["value"]; yield from ([v] if isinstance(v, str) else v)


def prepare(ver, fabric, say, prog):
    say("Pobieram informacje o wersji…")
    vm = http("https://piston-meta.mojang.com/mc/game/version_manifest_v2.json")
    j = http(next(x["url"] for x in vm["versions"] if x["id"] == ver))
    libs, jvm, game, main = list(j["libraries"]), list(j["arguments"]["jvm"]), list(j["arguments"]["game"]), j["mainClass"]
    if fabric:
        lv = next(x["loader"]["version"] for x in http(f"https://meta.fabricmc.net/v2/versions/loader/{ver}") if x["loader"]["stable"])
        f = http(f"https://meta.fabricmc.net/v2/versions/loader/{ver}/{lv}/profile/json")
        libs += f["libraries"]; jvm += f["arguments"].get("jvm", []); game += f["arguments"].get("game", []); main = f["mainClass"]
    jobs, cp = [], []
    for l in libs:
        if not ok(l.get("rules")): continue
        a = l.get("downloads", {}).get("artifact")
        if a: rel, url = a["path"], a["url"]
        else:
            g, n, vv = l["name"].split(":")[:3]
            rel = f"{g.replace('.', '/')}/{n}/{vv}/{n}-{vv}.jar"; url = l["url"] + rel
        cp.append(os.path.join(ROOT, "libraries", rel)); jobs.append((url, cp[-1]))
    ai = j["assetIndex"]; idx = os.path.join(ROOT, "assets", "indexes", ai["id"] + ".json"); get(ai["url"], idx)
    with open(idx) as fh: objs = json.load(fh)["objects"].values()
    for h in {o["hash"] for o in objs}:
        jobs.append((f"https://resources.download.minecraft.net/{h[:2]}/{h}", os.path.join(ROOT, "assets", "objects", h[:2], h)))
    jar = os.path.join(ROOT, "versions", ver, ver + ".jar"); cp.append(jar); jobs.append((j["downloads"]["client"]["url"], jar))
    todo = [x for x in jobs if not os.path.exists(x[1])]
    say(f"Pobieram pliki gry: {len(todo)}…" if todo else "Pliki gotowe.")
    with ThreadPoolExecutor(16) as ex:
        for i, _ in enumerate(ex.map(lambda x: get(*x), todo), 1): prog(i, len(todo))
    return dict(cp=cp, jvm=jvm, game=game, main=main, assets=ai["id"])


def java_path():
    exe = "javaw.exe" if OS == "windows" else "java"
    for r, _, fs in os.walk(os.path.join(ROOT, "java")):
        if exe in fs and os.path.basename(r) == "bin": return os.path.join(r, exe)


def get_java(say):  # własna Java 21 w ~/.kostka/java - pobierana raz, użytkownik nic nie instaluje
    if java_path(): return java_path()
    say("Pobieram Javę 21 (jednorazowo, ok. 50 MB)…")
    win = OS == "windows"; arc = os.path.join(ROOT, "java.zip" if win else "java.tgz")
    get("https://api.adoptium.net/v3/binary/latest/21/ga/%s/%s/jre/hotspot/normal/eclipse" % (
        {"windows": "windows", "osx": "mac", "linux": "linux"}[OS], "aarch64" if ARCH == "arm64" else "x64"), arc)
    shutil.unpack_archive(arc, os.path.join(ROOT, "java"), "zip" if win else "gztar"); os.remove(arc)
    return java_path()


def build_cmd(ver, acc, ram, info, java):
    gd = os.path.join(ROOT, "instances", ver); nat = os.path.join(gd, "natives")
    sub = {"natives_directory": nat, "launcher_name": "Kostka", "launcher_version": "1.0", "classpath": os.pathsep.join(info["cp"]),
           "auth_player_name": acc["name"], "version_name": ver, "game_directory": gd, "assets_root": os.path.join(ROOT, "assets"),
           "assets_index_name": info["assets"], "auth_uuid": acc["uuid"], "auth_access_token": acc["token"],
           "user_type": "msa", "version_type": "release"}
    fill = lambda s: re.sub(r"\$\{(\w+)\}", lambda m: sub.get(m.group(1), ""), s)
    return gd, [java, f"-Xmx{ram}G"] + [fill(a) for a in flat(info["jvm"])] + [info["main"]] + [fill(a) for a in flat(info["game"])]


# ---------- mody (Modrinth) ----------
def mods_dir(ver): return os.path.join(ROOT, "instances", ver, "mods")


def search(q, ver):
    facets = json.dumps([["project_type:mod"], ["categories:fabric"], [f"versions:{ver}"]])
    return http("https://api.modrinth.com/v2/search?" + urllib.parse.urlencode({"query": q, "facets": facets, "limit": 20})).get("hits", [])


def install_mod(pid, ver, seen=None):
    seen = set() if seen is None else seen
    if pid in seen: return []
    seen.add(pid)
    vs = http(f"https://api.modrinth.com/v2/project/{pid}/version?" + urllib.parse.urlencode(
        {"loaders": '["fabric"]', "game_versions": json.dumps([ver])}))
    if not isinstance(vs, list) or not vs: raise RuntimeError(f"ten mod nie ma wersji dla {ver}")
    f = next((x for x in vs[0]["files"] if x["primary"]), vs[0]["files"][0])
    get(f["url"], os.path.join(mods_dir(ver), f["filename"]))
    out = [f["filename"]]
    for d in vs[0]["dependencies"]:  # np. Fabric API instaluje się samo
        if d["dependency_type"] == "required" and d.get("project_id"): out += install_mod(d["project_id"], ver, seen)
    return out


# ---------- interfejs ----------
def logo(c):  # logo: zielona izometryczna kostka
    for pts, col in (([32, 6, 56, 19, 32, 32, 8, 19], "#8CFFC4"), ([8, 19, 32, 32, 32, 58, 8, 45], "#1FBF7A"),
                     ([56, 19, 32, 32, 32, 58, 56, 45], "#0F8A5A")):
        c.create_polygon(pts, fill=col, outline="")


def btn(parent, text, cmd, nav=False, big=False):
    return tk.Button(parent, text=text, command=cmd, relief="flat", bd=0, cursor="hand2", anchor="w" if nav else "center",
                     bg=PN if nav else AC, fg=TX if nav else BG, activebackground=AC, activeforeground=BG,
                     font=("Segoe UI", 16 if big else 11, "bold"), padx=26 if big else 14, pady=10 if big else 7)


def lab(parent, text, **k):
    return tk.Label(parent, text=text, bg=k.pop("bg", BG), fg=k.pop("fg", DM), font=("Segoe UI", k.pop("size", 10)), **k)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Kostka Client"); self.geometry("800x520"); self.configure(bg=BG); self.resizable(False, False)
        self.cfg, self.acc, self.hits, self.pages = load(), None, [], {}
        self.ver, self.fab, self.ram = tk.StringVar(), tk.BooleanVar(value=True), tk.IntVar(value=self.cfg.get("ram", 4))
        side = tk.Frame(self, bg=PN, width=190); side.pack(side="left", fill="y"); side.pack_propagate(False)
        c = tk.Canvas(side, width=64, height=64, bg=PN, highlightthickness=0); c.pack(pady=(28, 6)); logo(c)
        tk.Label(side, text="KOSTKA", font=("Segoe UI", 20, "bold"), fg=AC, bg=PN).pack()
        lab(side, "C L I E N T", bg=PN, size=8).pack(pady=(0, 24))
        for key, name in (("play", "▶   Graj"), ("mods", "🧩   Mody")):
            btn(side, name, lambda k=key: self.show(k), nav=True).pack(fill="x", padx=14, pady=3)
        self.who = lab(side, "Niezalogowano", bg=PN, wraplength=160); self.who.pack(side="bottom", pady=(0, 18))
        btn(side, "Zaloguj (Microsoft)", lambda: self.job(self.do_login)).pack(side="bottom", fill="x", padx=14, pady=6)

        main = tk.Frame(self, bg=BG); main.pack(side="right", fill="both", expand=True)
        self.msg = lab(main, "", size=11, justify="left", wraplength=560); self.msg.pack(side="bottom", anchor="w", padx=30, pady=(4, 20))
        self.bar = ttk.Progressbar(main, length=540); self.bar.pack(side="bottom", anchor="w", padx=30)

        p = self.pages["play"] = tk.Frame(main, bg=BG)
        lab(p, "Wersja Minecraft").pack(anchor="w", padx=30, pady=(40, 4))
        self.cb = ttk.Combobox(p, textvariable=self.ver, state="readonly", width=20); self.cb.pack(anchor="w", padx=30)
        tk.Checkbutton(p, text="Fabric (potrzebny do modów)", variable=self.fab, fg=TX, bg=BG, selectcolor=PN,
                       activebackground=BG, activeforeground=TX).pack(anchor="w", padx=30, pady=14)
        lab(p, "Pamięć RAM (GB)").pack(anchor="w", padx=30, pady=(0, 4))
        tk.Spinbox(p, from_=1, to=32, textvariable=self.ram, width=6).pack(anchor="w", padx=30)
        btn(p, "GRAJ", self.play, big=True).pack(anchor="w", padx=30, pady=30)

        m = self.pages["mods"] = tk.Frame(main, bg=BG)
        row = tk.Frame(m, bg=BG); row.pack(fill="x", padx=30, pady=(36, 10))
        self.q = tk.Entry(row, bg=PN, fg=TX, insertbackground=TX, relief="flat", font=("Segoe UI", 12))
        self.q.pack(side="left", fill="x", expand=True, ipady=6)
        self.q.bind("<Return>", lambda e: self.search())
        btn(row, "Szukaj", self.search).pack(side="left", padx=(8, 0))
        self.lb = tk.Listbox(m, bg=PN, fg=TX, relief="flat", height=12, font=("Segoe UI", 11), highlightthickness=0,
                             selectbackground=AC, selectforeground=BG, exportselection=False)
        self.lb.pack(fill="x", padx=30)
        row = tk.Frame(m, bg=BG); row.pack(fill="x", padx=30, pady=12)
        btn(row, "Zainstaluj zaznaczony", self.install).pack(side="left")
        btn(row, "Dodaj plik .jar…", self.add_jar).pack(side="left", padx=8)
        btn(row, "Folder modów", self.open_dir).pack(side="left")
        self.show("play"); self.job(self.init)

    def show(self, k):
        for p in self.pages.values(): p.pack_forget()
        self.pages[k].pack(fill="both", expand=True)

    def say(self, t): self.after(0, lambda: self.msg.config(text=t))
    def prog(self, i, n): self.after(0, lambda: self.bar.config(maximum=max(n, 1), value=i))

    def job(self, fn, *a):
        def run():
            try: fn(*a)
            except Exception as e: self.say(f"Błąd: {e}")
        threading.Thread(target=run, daemon=True).start()

    def init(self):
        vs = []
        for x in http("https://piston-meta.mojang.com/mc/game/version_manifest_v2.json")["versions"]:
            if x["type"] == "release": vs.append(x["id"])
            if x["id"] == "1.19": break
        self.after(0, lambda: (self.cb.config(values=vs), self.ver.set(self.cfg.get("ver", vs[0]))))
        if self.cfg.get("refresh"): self.do_login()

    def do_login(self):
        self.acc = login(self.say, self.cfg)
        self.after(0, lambda: self.who.config(text="✔ " + self.acc["name"], fg=AC)); self.say("Zalogowano jako " + self.acc["name"])

    def play(self):
        if not self.acc: return messagebox.showinfo("Kostka", "Najpierw zaloguj się kontem Microsoft.")
        self.cfg.update(ver=self.ver.get(), ram=self.ram.get()); save(self.cfg)
        self.job(self.start, self.ver.get(), self.fab.get(), self.ram.get())

    def start(self, v, fab, ram):
        info = prepare(v, fab, self.say, self.prog)
        gd, cmd = build_cmd(v, self.acc, ram, info, get_java(self.say))
        os.makedirs(os.path.join(gd, "mods"), exist_ok=True); os.makedirs(os.path.join(gd, "natives"), exist_ok=True)
        self.say(f"Uruchamianie Minecraft {v}…")
        log = os.path.join(gd, "kostka.log")
        with open(log, "w") as lg: rc = subprocess.Popen(cmd, cwd=gd, stdout=lg, stderr=subprocess.STDOUT).wait()
        self.say("Gra zamknięta." if rc == 0 else f"Gra zakończyła się błędem (kod {rc}). Log: {log}")

    def search(self): self.job(self.find, self.q.get(), self.ver.get())

    def find(self, q, v):
        self.hits = search(q, v)
        self.say(f"Znaleziono modów: {len(self.hits)}" if self.hits else f"Brak wyników dla {v} (Fabric).")
        def fill():
            self.lb.delete(0, "end")
            for h in self.hits: self.lb.insert("end", f"{h['title']} — {h['description'][:70]}")
        self.after(0, fill)

    def install(self):
        sel = self.lb.curselection()
        if not sel: return self.say("Zaznacz mod na liście.")
        self.job(self.add, self.hits[sel[0]]["project_id"], self.ver.get())

    def add(self, pid, v):
        self.say("Pobieram mod…"); self.say("Zainstalowano: " + ", ".join(install_mod(pid, v)))

    def add_jar(self):
        d = mods_dir(self.ver.get()); os.makedirs(d, exist_ok=True)
        for f in filedialog.askopenfilenames(filetypes=[("Mody", "*.jar")]): shutil.copy(f, d)
        self.say("Dodano pliki do folderu modów.")

    def open_dir(self):
        d = mods_dir(self.ver.get()); os.makedirs(d, exist_ok=True)
        if OS == "windows": os.startfile(d)
        else: subprocess.Popen(["open" if OS == "osx" else "xdg-open", d])


if __name__ == "__main__":
    App().mainloop()
