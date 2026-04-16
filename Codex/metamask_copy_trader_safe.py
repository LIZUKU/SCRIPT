"""
Safe Copy-Trading Dashboard (Educational / Paper Trading)

Important:
- This app DOES NOT connect to MetaMask private keys.
- This app DOES NOT execute real blockchain transactions.
- It is intended to prototype UX, risk controls, and data flow in paper mode.

Build .exe (Windows):
    pyinstaller --onefile --windowed Codex/metamask_copy_trader_safe.py
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox


@dataclass
class TraderStats:
    wallet: str
    win_rate: float
    pnl_30d: float
    sharpe: float
    last_action: str = "HOLD"


@dataclass
class BotConfig:
    metamask_public_address: str = ""
    max_trade_usd: float = 50.0
    daily_budget_usd: float = 200.0
    stop_loss_pct: float = 5.0
    take_profit_pct: float = 10.0
    slippage_pct: float = 1.0
    tracked_wallets: int = 10


@dataclass
class AppState:
    running: bool = False
    paper_balance_usd: float = 1_000.0
    spent_today_usd: float = 0.0
    open_positions: int = 0
    trades_today: int = 0
    log: list[str] = field(default_factory=list)


class SafeCopyTraderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("MetaMask Copy Trader - SAFE PAPER MODE")
        self.root.geometry("1180x760")

        self.config = BotConfig()
        self.state = AppState()
        self.traders: list[TraderStats] = self._seed_traders()

        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None

        self._build_ui()
        self._refresh_ui()

    def _seed_traders(self) -> list[TraderStats]:
        wallets = []
        for i in range(1, 16):
            suffix = "".join(random.choice("0123456789abcdef") for _ in range(6))
            wallets.append(
                TraderStats(
                    wallet=f"0xA{i:02d}{suffix}...{suffix[::-1]}",
                    win_rate=round(random.uniform(45, 78), 1),
                    pnl_30d=round(random.uniform(-8, 42), 2),
                    sharpe=round(random.uniform(0.4, 2.7), 2),
                )
            )
        return wallets

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="both", expand=True)

        left = ttk.Frame(top)
        left.pack(side="left", fill="y", padx=(0, 10))

        right = ttk.Frame(top)
        right.pack(side="left", fill="both", expand=True)

        self._build_config_panel(left)
        self._build_controls(left)
        self._build_stats_panel(left)

        self._build_trader_table(right)
        self._build_charts_panel(right)
        self._build_log_panel(right)

    def _build_config_panel(self, parent: ttk.Frame) -> None:
        card = ttk.LabelFrame(parent, text="Connexion / Sécurité", padding=10)
        card.pack(fill="x", pady=(0, 10))

        self.address_var = tk.StringVar(value=self.config.metamask_public_address)
        self.max_trade_var = tk.DoubleVar(value=self.config.max_trade_usd)
        self.daily_budget_var = tk.DoubleVar(value=self.config.daily_budget_usd)
        self.stop_loss_var = tk.DoubleVar(value=self.config.stop_loss_pct)
        self.take_profit_var = tk.DoubleVar(value=self.config.take_profit_pct)
        self.slippage_var = tk.DoubleVar(value=self.config.slippage_pct)
        self.wallets_var = tk.IntVar(value=self.config.tracked_wallets)

        fields = [
            ("Adresse publique MetaMask", self.address_var),
            ("Montant max / trade (USD)", self.max_trade_var),
            ("Budget journalier (USD)", self.daily_budget_var),
            ("Stop-loss (%)", self.stop_loss_var),
            ("Take-profit (%)", self.take_profit_var),
            ("Slippage max (%)", self.slippage_var),
            ("Nb wallets à copier", self.wallets_var),
        ]

        for idx, (label, var) in enumerate(fields):
            ttk.Label(card, text=label).grid(row=idx, column=0, sticky="w", pady=3)
            entry = ttk.Entry(card, textvariable=var, width=28)
            entry.grid(row=idx, column=1, sticky="ew", pady=3, padx=(8, 0))

        card.columnconfigure(1, weight=1)

        ttk.Button(card, text="Sauvegarder paramètres", command=self._save_config).grid(
            row=len(fields), column=0, columnspan=2, sticky="ew", pady=(10, 0)
        )

    def _build_controls(self, parent: ttk.Frame) -> None:
        card = ttk.LabelFrame(parent, text="Bot Controls", padding=10)
        card.pack(fill="x", pady=(0, 10))

        self.mode_label = ttk.Label(card, text="Mode: PAPER TRADING (aucun ordre réel)")
        self.mode_label.pack(anchor="w", pady=(0, 8))

        btns = ttk.Frame(card)
        btns.pack(fill="x")
        ttk.Button(btns, text="Démarrer", command=self.start_bot).pack(side="left", expand=True, fill="x", padx=(0, 4))
        ttk.Button(btns, text="Stop", command=self.stop_bot).pack(side="left", expand=True, fill="x", padx=(4, 0))

    def _build_stats_panel(self, parent: ttk.Frame) -> None:
        card = ttk.LabelFrame(parent, text="Stats en temps réel (simulées)", padding=10)
        card.pack(fill="x")

        self.balance_var = tk.StringVar()
        self.spent_var = tk.StringVar()
        self.positions_var = tk.StringVar()
        self.trades_var = tk.StringVar()
        self.signal_var = tk.StringVar(value="Signal marché: NEUTRE")

        for lbl, var in [
            ("Solde paper:", self.balance_var),
            ("Dépensé aujourd'hui:", self.spent_var),
            ("Positions ouvertes:", self.positions_var),
            ("Trades aujourd'hui:", self.trades_var),
            ("", self.signal_var),
        ]:
            row = ttk.Frame(card)
            row.pack(fill="x", pady=2)
            if lbl:
                ttk.Label(row, text=lbl, width=22).pack(side="left")
            ttk.Label(row, textvariable=var).pack(side="left")

    def _build_trader_table(self, parent: ttk.Frame) -> None:
        card = ttk.LabelFrame(parent, text="Top wallets (simulés)", padding=10)
        card.pack(fill="both", expand=True, pady=(0, 10))

        cols = ("wallet", "win", "pnl", "sharpe", "last")
        self.tree = ttk.Treeview(card, columns=cols, show="headings", height=10)
        self.tree.heading("wallet", text="Wallet")
        self.tree.heading("win", text="Win Rate %")
        self.tree.heading("pnl", text="PnL 30j %")
        self.tree.heading("sharpe", text="Sharpe")
        self.tree.heading("last", text="Dernière action")

        for col, width in [("wallet", 260), ("win", 90), ("pnl", 90), ("sharpe", 70), ("last", 120)]:
            self.tree.column(col, width=width, anchor="center")

        self.tree.pack(fill="both", expand=True)

    def _build_charts_panel(self, parent: ttk.Frame) -> None:
        card = ttk.LabelFrame(parent, text="Chart live (sparkline simplifiée)", padding=10)
        card.pack(fill="x", pady=(0, 10))

        self.canvas = tk.Canvas(card, height=130, background="#10151f", highlightthickness=0)
        self.canvas.pack(fill="x", expand=True)

        self.price_points = [100 + random.uniform(-3, 3) for _ in range(60)]

    def _build_log_panel(self, parent: ttk.Frame) -> None:
        card = ttk.LabelFrame(parent, text="Journal bot", padding=10)
        card.pack(fill="both", expand=True)

        self.log_text = tk.Text(card, height=12, wrap="word")
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")

    def _save_config(self) -> None:
        try:
            self.config.metamask_public_address = self.address_var.get().strip()
            self.config.max_trade_usd = float(self.max_trade_var.get())
            self.config.daily_budget_usd = float(self.daily_budget_var.get())
            self.config.stop_loss_pct = float(self.stop_loss_var.get())
            self.config.take_profit_pct = float(self.take_profit_var.get())
            self.config.slippage_pct = float(self.slippage_var.get())
            self.config.tracked_wallets = int(self.wallets_var.get())
        except ValueError:
            messagebox.showerror("Erreur", "Paramètres invalides.")
            return

        if not self.config.metamask_public_address.startswith("0x"):
            messagebox.showwarning(
                "Attention",
                "Adresse MetaMask absente/invalide. Seule une adresse publique est acceptée.",
            )

        self._log("Paramètres sauvegardés.")

    def start_bot(self) -> None:
        with self._lock:
            if self.state.running:
                return
            self.state.running = True

        self._log("Bot démarré en PAPER MODE.")
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()

    def stop_bot(self) -> None:
        with self._lock:
            self.state.running = False
        self._log("Bot arrêté.")

    def _loop(self) -> None:
        while True:
            with self._lock:
                if not self.state.running:
                    break

            self._simulate_market_tick()
            self.root.after(0, self._refresh_ui)
            time.sleep(1.0)

    def _simulate_market_tick(self) -> None:
        drift = random.uniform(-1.6, 1.6)
        new_price = max(40, self.price_points[-1] + drift)
        self.price_points = (self.price_points + [new_price])[-80:]

        for t in self.traders:
            t.win_rate = min(95, max(10, round(t.win_rate + random.uniform(-0.8, 0.8), 1)))
            t.pnl_30d = round(t.pnl_30d + random.uniform(-0.6, 0.6), 2)
            t.sharpe = round(max(-1.0, t.sharpe + random.uniform(-0.08, 0.08)), 2)
            t.last_action = random.choice(["BUY", "SELL", "HOLD"])

        ranked = sorted(self.traders, key=lambda x: (x.pnl_30d, x.sharpe, x.win_rate), reverse=True)
        selected = ranked[: max(1, min(self.config.tracked_wallets, len(ranked)))]

        signal = "BULLISH" if drift > 0.55 else "BEARISH" if drift < -0.55 else "NEUTRE"
        self.signal_var.set(f"Signal marché: {signal}")

        should_trade = signal in {"BULLISH", "BEARISH"} and random.random() < 0.35
        if should_trade and self.state.spent_today_usd < self.config.daily_budget_usd:
            trade_size = min(self.config.max_trade_usd, self.config.daily_budget_usd - self.state.spent_today_usd)
            if trade_size > 0:
                outcome = random.uniform(-self.config.stop_loss_pct, self.config.take_profit_pct)
                pnl = trade_size * (outcome / 100)
                self.state.paper_balance_usd += pnl
                self.state.spent_today_usd += trade_size
                self.state.trades_today += 1
                self.state.open_positions = max(0, self.state.open_positions + random.choice([-1, 0, 1]))
                top_wallet = selected[0].wallet if selected else "N/A"
                self._log(
                    f"{signal} | Copie simulée wallet {top_wallet} | taille ${trade_size:.2f} | PnL ${pnl:.2f}"
                )

    def _refresh_ui(self) -> None:
        self.balance_var.set(f"${self.state.paper_balance_usd:,.2f}")
        self.spent_var.set(f"${self.state.spent_today_usd:,.2f} / ${self.config.daily_budget_usd:,.2f}")
        self.positions_var.set(str(self.state.open_positions))
        self.trades_var.set(str(self.state.trades_today))

        for item in self.tree.get_children():
            self.tree.delete(item)

        ranked = sorted(self.traders, key=lambda x: (x.pnl_30d, x.sharpe, x.win_rate), reverse=True)
        for t in ranked[: max(1, min(self.config.tracked_wallets, len(ranked)))]:
            self.tree.insert("", "end", values=(t.wallet, t.win_rate, t.pnl_30d, t.sharpe, t.last_action))

        self._draw_chart()

    def _draw_chart(self) -> None:
        self.canvas.delete("all")
        w = self.canvas.winfo_width() or 800
        h = self.canvas.winfo_height() or 130

        if len(self.price_points) < 2:
            return

        min_p = min(self.price_points)
        max_p = max(self.price_points)
        span = max(1e-9, max_p - min_p)

        coords = []
        for i, p in enumerate(self.price_points):
            x = (i / (len(self.price_points) - 1)) * (w - 20) + 10
            y = h - (((p - min_p) / span) * (h - 20) + 10)
            coords.extend([x, y])

        self.canvas.create_line(*coords, fill="#00E5A8", width=2, smooth=True)
        self.canvas.create_text(10, 10, text=f"min {min_p:.2f}", fill="#90A4AE", anchor="nw")
        self.canvas.create_text(w - 10, 10, text=f"max {max_p:.2f}", fill="#90A4AE", anchor="ne")

    def _log(self, message: str) -> None:
        timestamp = datetime.utcnow().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        self.state.log.append(line)

        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")


def main() -> None:
    root = tk.Tk()
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")

    app = SafeCopyTraderApp(root)
    app._log("Application prête. Configure tes paramètres puis clique Démarrer.")
    root.mainloop()


if __name__ == "__main__":
    main()
