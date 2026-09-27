//+------------------------------------------------------------------+
//|                                               ANON_Levels_v2.mq5 |
//| Display only: levels, Ghost calls, Ω regime, Risk and Zen panel. |
//| Mirrors anon/ghost.py, anon/omega.py and anon/risk.py.           |
//| This indicator never places, modifies or closes orders.          |
//+------------------------------------------------------------------+
#property copyright   "ANON / BlackSlash"
#property version     "2.00"
#property description "ANON levels + Ghost / Ω / Risk / Zen panel (display only, never trades)"
#property indicator_chart_window
#property indicator_buffers 4
#property indicator_plots   4

#property indicator_label1  "Ω EMA fast"
#property indicator_type1   DRAW_LINE
#property indicator_color1  clrDeepSkyBlue
#property indicator_width1  1
#property indicator_label2  "Ω EMA slow"
#property indicator_type2   DRAW_LINE
#property indicator_color2  clrOrange
#property indicator_width2  1
#property indicator_label3  "Ghost A call"
#property indicator_type3   DRAW_ARROW
#property indicator_color3  clrLime
#property indicator_width3  2
#property indicator_label4  "Ghost B call"
#property indicator_type4   DRAW_ARROW
#property indicator_color4  clrAqua
#property indicator_width4  2

//--- keep these defaults equal to anon/config.py (tests/test_mql5_parity.py checks it)
input group "Levels (ใบส่งมอบ)"
input double InpAZoneBot       = 83000.0;   // A bot / inv
input double InpAZoneTop       = 83500.0;   // A top
input double InpBInvRef        = 84400.0;   // B inv ref (อ้างอิง ไม่ใช่ entry/stop)
input double InpGrayCenter     = 84700.0;   // เทา NO CHASE
input double InpGrayHalf       = 150.0;     // ครึ่งความกว้างเทา (ต้องยืนยัน)
input double InpTP1            = 85500.0;   // TP1
input double InpLiqTop         = 87000.0;   // liq บน

input group "Ghost"
input double InpStopBuffer     = 50.0;      // stop = low ของ sweep/swing - ค่านี้
input int    InpSwingLookback  = 6;         // แท่งที่ใช้หา swing low ของ B
input int    InpBreakoutExpiry = 12;        // B armed ได้กี่แท่ง
input double InpRetestTol      = 100.0;     // รีเทสได้สูงกว่าขอบเทาไม่เกิน
input double InpMinRR          = 1.0;       // RR ขั้นต่ำ (ต้องยืนยัน)

input group "Risk"
input double InpLot            = 0.01;
input double InpMaxLot         = 0.01;
input double InpRiskPct        = 0.5;       // % ต่อไม้
input double InpDailyPct       = 2.0;       // % ต่อวัน
input double InpPeakWarnPct    = 5.0;       // เตือน DD จาก peak
input long   InpVetoTicket     = 208190412; // ไม้หลุดแผน
input long   InpBotMagic       = 7700700;   // magic ของ engine
input bool   InpVetoUnmanaged  = true;      // มีไม้นอกระบบ = VETO
input double InpDayUtcOffset   = 7.0;       // ตัดวันตามเวลาไทย

input group "Ω"
input int    InpEmaFast        = 20;
input int    InpEmaSlow        = 50;
input int    InpAtrPeriod      = 14;
input double InpShockMult      = 3.0;
input bool   InpOmegaVeto      = true;      // Ω ไม่เอื้อ = VETO (ต้องยืนยัน)

input group "Display"
input int    InpMarkerBars     = 500;       // ย้อนหลังกี่แท่งสำหรับลูกศร Ghost
input bool   InpShowPanel      = true;
input int    InpPanelX         = 10;
input int    InpPanelY         = 25;
input int    InpPanelWidth     = 560;
input string InpFont           = "Consolas";  // Cyberpunk: monospace
input int    InpFontSize       = 9;
input color  InpClrAZone       = C'0,70,85';     // neon teal (dim)
input color  InpClrGray        = C'75,20,95';    // violet haze
input color  InpClrPanel       = C'8,4,20';      // near-black purple
input color  InpClrPanelBorder = C'255,0,170';   // neon magenta
input color  InpClrAccent      = C'255,0,170';   // section headers
input color  InpClrText        = C'0,240,255';   // neon cyan
input color  InpClrMuted       = C'150,120,210'; // lavender
input color  InpClrGood        = C'57,255,20';   // acid green
input color  InpClrBad         = C'255,40,110';  // hot pink
input color  InpClrWarn        = C'255,230,0';   // neon yellow

input bool   InpCyberChart     = true;      // ธีม Cyberpunk ทั้งกราฟ (คืนสีเดิมเมื่อถอดอินดิเคเตอร์)

input group "Engine (Python) บนกราฟ"
input bool   InpShowEngine     = true;              // แสดงสิ่งที่ engine ทำ (ไฟล์จาก run.bat)
input string InpFeedFile       = "ANON_feed.txt";   // ใน Common\Files ของ MT5
input int    InpFeedMaxAgeSec  = 120;               // เกินนี้ถือว่า engine หยุดทำงาน
input int    InpFeedEvents     = 4;                 // จำนวนเหตุการณ์ล่าสุดในแผง
input color  InpClrEngineBuy   = clrLime;           // ลูกศร: engine จะกด (dry-run/order)
input color  InpClrEngineVeto  = clrOrange;         // ลูกศร: เรียกแล้วแต่โดน veto
input color  InpClrAutoLevels  = clrAqua;           // เส้นระดับอัตโนมัติจาก engine

#define PFX         "ANON2_"
#define WINDOW_BARS 300   // same window the Python engine passes to Ghost/Ω

struct GhostCall
  {
   int      setup;      // 0 none, 1 A, 2 B
   string   variant;
   double   entry;
   double   stop;
   double   rr;
   string   reason;
   datetime bar_time;
  };

struct RegimeInfo
  {
   string regime;
   bool   favorable;
   string reason;
   double ema_fast;
   double ema_slow;
   double atr;
  };

double     BufEmaFast[], BufEmaSlow[], BufCallA[], BufCallB[];
MqlRates   g_rates[];
int        g_n = 0;
datetime   g_last_h1 = 0;
datetime   g_markers_for = 0;
GhostCall  g_call;
RegimeInfo g_regime;
int        g_breakout = -1;
double     g_b_swing_stop = 0.0;
string     g_txt[];
color      g_col[];
int        g_lines = 0;
int        g_prev_lines = 0;

//+------------------------------------------------------------------+
//| small helpers                                                    |
//+------------------------------------------------------------------+
int    IMax(const int a, const int b) { return a > b ? a : b; }
int    IMin(const int a, const int b) { return a < b ? a : b; }
double GrayLow()  { return InpGrayCenter - InpGrayHalf; }
double GrayHigh() { return InpGrayCenter + InpGrayHalf; }

double RewardRisk(const double entry, const double stop, const double tp)
  {
   double risk = entry - stop;
   if(risk <= 0.0)
      return 0.0;
   return (tp - entry) / risk;
  }

string Num(const double v, const int digits)
  {
   string s   = DoubleToString(MathAbs(v), digits);
   int    dot = StringFind(s, ".");
   string ip  = (dot < 0) ? s : StringSubstr(s, 0, dot);
   string fp  = (dot < 0) ? "" : StringSubstr(s, dot);
   string out = "";
   int    len = StringLen(ip);
   for(int i = 0; i < len; i++)
     {
      if(i > 0 && (len - i) % 3 == 0)
         out += ",";
      out += StringSubstr(ip, i, 1);
     }
   return (v < 0 ? "-" : "") + out + fp;
  }

string Px(const double v) { return Num(v, 1); }
string Money(const double v) { return Num(v, 2); }

//+------------------------------------------------------------------+
//| Ghost — mirrors Ghost.evaluate in anon/ghost.py                  |
//+------------------------------------------------------------------+
int ArmedBreakout(const MqlRates &r[], const int i)
  {
   double gh = GrayHigh();
   int lo = IMax(1, i - InpBreakoutExpiry);
   for(int j = i; j >= lo; j--)
     {
      if(r[j].close <= gh)
         return -1;
      if(r[j - 1].close <= gh)
         return j;
     }
   return -1;
  }

double SwingStop(const MqlRates &r[], const int i)
  {
   int from = IMax(0, i - InpSwingLookback + 1);
   double lo = r[from].low;
   for(int k = from + 1; k <= i; k++)
      lo = MathMin(lo, r[k].low);
   return lo - InpStopBuffer;
  }

void GhostEvaluate(const MqlRates &r[], const int i, GhostCall &c)
  {
   c.setup = 0;
   c.variant = "";
   c.entry = 0.0;
   c.stop = 0.0;
   c.rr = 0.0;
   c.reason = "no_data";
   c.bar_time = 0;
   if(i < 0)
      return;
   c.bar_time = r[i].time;
   double cl = r[i].close;
   if(cl >= GrayLow() && cl <= GrayHigh())
     {
      c.reason = "inside_gray_no_chase";
      return;
     }
   if(r[i].low <= InpAZoneTop && cl >= InpAZoneBot && cl < GrayLow())
     {
      c.setup = 1;
      c.variant = (r[i].low < InpAZoneBot) ? "sweep_reclaim" : "zone";
      c.stop = MathMin(r[i].low, InpAZoneBot) - InpStopBuffer;
     }
   else
      if(cl > GrayHigh())
        {
         int j = ArmedBreakout(r, i);
         if(j >= 0 && j < i && r[i].low <= GrayHigh() + InpRetestTol)
           {
            c.setup = 2;
            c.variant = "breakout_retest";
            c.stop = SwingStop(r, i);
           }
        }
   if(c.setup == 0)
     {
      c.reason = "no_setup";
      return;
     }
   string name = (c.setup == 1) ? "A" : "B";
   c.entry = cl;
   c.rr = RewardRisk(cl, c.stop, InpTP1);
   if(c.entry >= InpTP1)
     {
      c.reason = name + ":entry_at_or_above_tp1";
      c.setup = 0;
      return;
     }
   if(c.rr < InpMinRR)
     {
      c.reason = name + ":rr_" + DoubleToString(c.rr, 2) + "_below_min";
      c.setup = 0;
      return;
     }
   c.reason = "call_" + name + "_" + c.variant;
  }

//+------------------------------------------------------------------+
//| Ω — mirrors Omega.rule_tag in anon/omega.py                      |
//+------------------------------------------------------------------+
double EmaLast(const MqlRates &r[], const int from, const int to, const int period)
  {
   double k = 2.0 / (period + 1);
   double e = r[from].close;
   for(int i = from + 1; i <= to; i++)
      e = e + k * (r[i].close - e);
   return e;
  }

double AtrLast(const MqlRates &r[], const int from, const int to, const int period)
  {
   double a = 0.0, sum = 0.0;
   for(int i = from; i <= to; i++)
     {
      double tr = r[i].high - r[i].low;
      if(i > from)
        {
         double pc = r[i - 1].close;
         tr = MathMax(tr, MathMax(MathAbs(r[i].high - pc), MathAbs(r[i].low - pc)));
        }
      int idx = i - from;
      if(idx < period)
        {
         sum += tr;
         a = sum / (idx + 1);
        }
      else
         a = (a * (period - 1) + tr) / period;
     }
   return a;
  }

void RuleTag(const MqlRates &r[], const int n, RegimeInfo &g)
  {
   g.ema_fast = 0.0;
   g.ema_slow = 0.0;
   g.atr = 0.0;
   int from = IMax(0, n - WINDOW_BARS);
   if(n - from < InpEmaSlow + 1)
     {
      g.regime = "unknown";
      g.favorable = false;
      g.reason = "ข้อมูล H1 ไม่พอ (ต้องมี " + IntegerToString(InpEmaSlow + 1) + " แท่ง)";
      return;
     }
   int last = n - 1;
   g.ema_fast = EmaLast(r, from, last, InpEmaFast);
   g.ema_slow = EmaLast(r, from, last, InpEmaSlow);
   g.atr = AtrLast(r, from, last - 1, InpAtrPeriod);
   double range = r[last].high - r[last].low;
   double cl = r[last].close;
   if(g.atr > 0.0 && range > InpShockMult * g.atr)
     {
      g.regime = "shock";
      g.reason = "แท่งล่าสุดกว้าง " + Px(range) + " > " + DoubleToString(InpShockMult, 1) + "×ATR";
     }
   else
      if(g.ema_fast > g.ema_slow && cl > g.ema_slow)
        {
         g.regime = "trend_up";
         g.reason = "EMA เร็ว > EMA ช้า";
        }
      else
         if(g.ema_fast < g.ema_slow && cl < g.ema_slow)
           {
            g.regime = "trend_down";
            g.reason = "EMA เร็ว < EMA ช้า";
           }
         else
           {
            g.regime = "range";
            g.reason = "EMA ปนกัน";
           }
   g.favorable = (g.regime == "trend_up" || g.regime == "range");
  }

//+------------------------------------------------------------------+
//| data refresh on each new closed H1 bar                           |
//+------------------------------------------------------------------+
void RefreshH1()
  {
   ArraySetAsSeries(g_rates, false);
   int got = CopyRates(_Symbol, PERIOD_H1, 1, WINDOW_BARS + InpMarkerBars, g_rates);
   g_n = (got > 0) ? got : 0;
   if(g_n < 2)
      return;
   GhostEvaluate(g_rates, g_n - 1, g_call);
   RuleTag(g_rates, g_n, g_regime);
   g_breakout = (g_rates[g_n - 1].close > GrayHigh()) ? ArmedBreakout(g_rates, g_n - 1) : -1;
   g_b_swing_stop = SwingStop(g_rates, g_n - 1);
  }

bool CheckNewH1()
  {
   datetime t = iTime(_Symbol, PERIOD_H1, 1);
   if(t == 0 || (t == g_last_h1 && g_n >= 2))
      return false;
   g_last_h1 = t;
   RefreshH1();
   return true;
  }

//+------------------------------------------------------------------+
//| chart objects                                                    |
//+------------------------------------------------------------------+
void Tag(const string id, const double price, const color clr, const string text)
  {
   string name = PFX + "T_" + id;
   datetime t = iTime(_Symbol, _Period, 0) + PeriodSeconds(_Period) * 2;
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_TEXT, 0, t, price);
   ObjectMove(0, name, 0, t, price);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetString(0, name, OBJPROP_FONT, InpFont);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, InpFontSize);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_LEFT_LOWER);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
  }

void HLine(const string id, const double price, const color clr, const ENUM_LINE_STYLE style,
           const int width, const string text)
  {
   string name = PFX + id;
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_HLINE, 0, 0, price);
   ObjectSetDouble(0, name, OBJPROP_PRICE, price);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_STYLE, style);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, width);
   ObjectSetInteger(0, name, OBJPROP_BACK, true);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetString(0, name, OBJPROP_TOOLTIP, text);
   Tag(id, price, clr, text);
  }

void Band(const string id, const double p1, const double p2, const color clr, const string text)
  {
   string name = PFX + id;
   int bars = Bars(_Symbol, _Period);
   datetime t1 = iTime(_Symbol, _Period, IMax(0, IMin(bars - 1, 5000)));
   datetime t2 = iTime(_Symbol, _Period, 0) + PeriodSeconds(_Period) * 60;
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_RECTANGLE, 0, t1, p1, t2, p2);
   ObjectMove(0, name, 0, t1, p1);
   ObjectMove(0, name, 1, t2, p2);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_FILL, true);
   ObjectSetInteger(0, name, OBJPROP_BACK, true);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   ObjectSetString(0, name, OBJPROP_TOOLTIP, text);
   Tag(id, p2, clr, text);
  }

void Remove(const string id)
  {
   ObjectDelete(0, PFX + id);
   ObjectDelete(0, PFX + "T_" + id);
  }

double BMaxEntry(const double stop)
  {
   return (InpTP1 + InpMinRR * stop) / (1.0 + InpMinRR);
  }

void DrawLevels()
  {
   Band("AZONE", InpAZoneBot, InpAZoneTop, InpClrAZone,
        "A โซน " + Px(InpAZoneBot) + "–" + Px(InpAZoneTop));
   Band("GRAY", GrayLow(), GrayHigh(), InpClrGray,
        "เทา NO CHASE " + Px(GrayLow()) + "–" + Px(GrayHigh()));
   HLine("ASTOP", InpAZoneBot - InpStopBuffer, InpClrBad, STYLE_DASH, 1,
         "A hard stop (โซน) " + Px(InpAZoneBot - InpStopBuffer) + " · sweep ใช้ low−" + Px(InpStopBuffer));
   HLine("BREF", InpBInvRef, InpClrMuted, STYLE_DOT, 1,
         "B ref " + Px(InpBInvRef) + " (อ้างอิง ไม่ใช่ entry/stop)");
   HLine("TP1", InpTP1, clrDodgerBlue, STYLE_SOLID, 2, "TP1 " + Px(InpTP1));
   HLine("LIQ", InpLiqTop, clrDarkOrange, STYLE_DASH, 1, "liq บน " + Px(InpLiqTop));

   if(g_call.setup != 0)
      HLine("CALLSTOP", g_call.stop, clrMagenta, STYLE_DASH, 2,
            "Ghost " + (g_call.setup == 1 ? "A" : "B") + " stop " + Px(g_call.stop));
   else
      Remove("CALLSTOP");

   if(g_breakout >= 0)
     {
      HLine("BSWING", g_b_swing_stop, clrMagenta, STYLE_DOT, 1, "B swing stop " + Px(g_b_swing_stop));
      HLine("BMAX", BMaxEntry(g_b_swing_stop), clrAqua, STYLE_DOT, 1,
            "B เข้าได้ไม่เกิน " + Px(BMaxEntry(g_b_swing_stop)) + " (RR≥" + DoubleToString(InpMinRR, 1) + ")");
     }
   else
     {
      Remove("BSWING");
      Remove("BMAX");
     }
  }

//+------------------------------------------------------------------+
//| panel                                                            |
//+------------------------------------------------------------------+
// Font size is in points and grows with Windows display scaling; pixel offsets do not, so scale them.
double Dpi()        { int d = (int)TerminalInfoInteger(TERMINAL_SCREEN_DPI); return d > 0 ? d / 96.0 : 1.0; }
int    Px2(const int px) { return (int)MathRound(px * Dpi()); }
int    LineHeight() { return Px2(InpFontSize * 2 + 2); }

void Line(const string s, const color c)
  {
   ArrayResize(g_txt, g_lines + 1);
   ArrayResize(g_col, g_lines + 1);
   g_txt[g_lines] = s;
   g_col[g_lines] = StringFind(s, "──") == 0 ? InpClrAccent : c;
   g_lines++;
  }

void PanelBg(const int lines)
  {
   string name = PFX + "BG";
   if(ObjectFind(0, name) < 0)
     {
      ObjectCreate(0, name, OBJ_RECTANGLE_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_BORDER_TYPE, BORDER_FLAT);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
     }
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, InpPanelX);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, InpPanelY);
   ObjectSetInteger(0, name, OBJPROP_XSIZE, Px2(InpPanelWidth));
   ObjectSetInteger(0, name, OBJPROP_YSIZE, Px2(12) + lines * LineHeight());
   ObjectSetInteger(0, name, OBJPROP_WIDTH, 2);
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, InpClrPanel);
   ObjectSetInteger(0, name, OBJPROP_COLOR, InpClrPanelBorder);
  }

void RenderPanel()
  {
   PanelBg(g_lines);
   for(int i = 0; i < g_lines; i++)
     {
      string name = PFX + "P" + IntegerToString(i);
      if(ObjectFind(0, name) < 0)
        {
         ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
         ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
         ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_LEFT_UPPER);
         ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
        }
      ObjectSetString(0, name, OBJPROP_FONT, InpFont);
      ObjectSetInteger(0, name, OBJPROP_FONTSIZE, InpFontSize);
      ObjectSetInteger(0, name, OBJPROP_XDISTANCE, InpPanelX + Px2(8));
      ObjectSetInteger(0, name, OBJPROP_YDISTANCE, InpPanelY + Px2(6) + i * LineHeight());
      ObjectSetString(0, name, OBJPROP_TEXT, g_txt[i]);
      ObjectSetInteger(0, name, OBJPROP_COLOR, g_col[i]);
     }
   for(int i = g_lines; i < g_prev_lines; i++)
      ObjectDelete(0, PFX + "P" + IntegerToString(i));
   g_prev_lines = g_lines;
  }

// Loss in account currency for InpLot between two prices (tick value, so no trade functions needed).
double RiskMoney(const double entry, const double stop)
  {
   double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(tick_value <= 0.0)
      tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   if(tick_size <= 0.0 || tick_value <= 0.0)
      return -1.0;
   return MathAbs(entry - stop) / tick_size * tick_value * InpLot;
  }

double DayStartBalance(const double balance)
  {
   long server_offset = (long)(TimeTradeServer() - TimeGMT());
   server_offset = (long)MathRound(server_offset / 900.0) * 900;
   long day_offset = (long)(InpDayUtcOffset * 3600.0);
   long local = (long)TimeGMT() + day_offset;
   long day_start_local = local - (local % 86400);
   datetime from = (datetime)(day_start_local - day_offset + server_offset);
   double realized = 0.0;
   if(HistorySelect(from, TimeTradeServer() + 3600))
     {
      int total = HistoryDealsTotal();
      for(int i = 0; i < total; i++)
        {
         ulong t = HistoryDealGetTicket(i);
         if(t == 0)
            continue;
         long type = HistoryDealGetInteger(t, DEAL_TYPE);
         if(type != DEAL_TYPE_BUY && type != DEAL_TYPE_SELL)
            continue;
         realized += HistoryDealGetDouble(t, DEAL_PROFIT) + HistoryDealGetDouble(t, DEAL_COMMISSION)
                     + HistoryDealGetDouble(t, DEAL_SWAP) + HistoryDealGetDouble(t, DEAL_FEE);
        }
     }
   return balance - realized;
  }

string Verdict(const bool ok) { return ok ? "ผ่าน" : "ไม่ผ่าน"; }

//+------------------------------------------------------------------+
//| engine feed: what the Python engine is doing (anon/chartfeed.py) |
//+------------------------------------------------------------------+
string g_feed_lines[];
int    g_feed_n = 0;

bool ReadFeed()
  {
   g_feed_n = 0;
   int h = FileOpen(InpFeedFile, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ | FILE_SHARE_WRITE,
                    '\t', CP_UTF8);
   if(h == INVALID_HANDLE)
      return false;
   while(!FileIsEnding(h))
     {
      string s = FileReadString(h);
      StringTrimRight(s);
      if(StringLen(s) == 0)
         continue;
      ArrayResize(g_feed_lines, g_feed_n + 1);
      g_feed_lines[g_feed_n] = s;
      g_feed_n++;
     }
   FileClose(h);
   return g_feed_n > 0;
  }

string FeedGet(const string key)
  {
   string prefix = key + "=";
   int pl = StringLen(prefix);
   for(int i = 0; i < g_feed_n; i++)
      if(StringSubstr(g_feed_lines[i], 0, pl) == prefix)
         return StringSubstr(g_feed_lines[i], pl);
   return "";
  }

int FeedAll(const string key, string &out[])
  {
   string prefix = key + "=";
   int pl = StringLen(prefix);
   int n = 0;
   ArrayResize(out, 0);
   for(int i = 0; i < g_feed_n; i++)
      if(StringSubstr(g_feed_lines[i], 0, pl) == prefix)
        {
         ArrayResize(out, n + 1);
         out[n] = StringSubstr(g_feed_lines[i], pl);
         n++;
        }
   return n;
  }

long FeedAgeSec()
  {
   string hb = FeedGet("heartbeat");
   if(StringLen(hb) == 0)
      return 999999;
   return (long)TimeGMT() - StringToInteger(hb);
  }

bool FeedFresh()
  {
   long age = FeedAgeSec();
   return g_feed_n > 0 && age >= -60 && age <= InpFeedMaxAgeSec;
  }

string AgeText(const long age)
  {
   if(age < 60)
      return IntegerToString(age < 0 ? 0 : age) + " วิ";
   if(age < 3600)
      return IntegerToString(age / 60) + " นาที";
   return IntegerToString(age / 3600) + " ชม.";
  }

long ServerOffsetSec()
  {
   long off = (long)(TimeTradeServer() - TimeGMT());
   return (long)MathRound(off / 900.0) * 900;
  }

string ThaiClock(const long epoch_utc)
  {
   return TimeToString((datetime)(epoch_utc + (long)(InpDayUtcOffset * 3600.0)), TIME_MINUTES);
  }

void EngineMarker(const string id, const datetime t, const double price, const color clr, const string tip)
  {
   string name = PFX + "EC_" + id;
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_ARROW_BUY, 0, t, price);
   ObjectMove(0, name, 0, t, price);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, 2);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   ObjectSetString(0, name, OBJPROP_TOOLTIP, tip);
  }

void DrawEngine()
  {
   string ids[5] = {"EA_ABOT", "EA_ATOP", "EA_GRAYL", "EA_GRAYH", "EA_TP1"};
   bool auto_levels = FeedFresh() && FeedGet("levels_source") == "auto" && FeedGet("levels_ok") == "1";
   if(auto_levels)
     {
      HLine(ids[0], StringToDouble(FeedGet("a_bot")), InpClrAutoLevels, STYLE_DASHDOT, 1, "AUTO A ล่าง " + Px(StringToDouble(FeedGet("a_bot"))));
      HLine(ids[1], StringToDouble(FeedGet("a_top")), InpClrAutoLevels, STYLE_DASHDOT, 1, "AUTO A บน " + Px(StringToDouble(FeedGet("a_top"))));
      HLine(ids[2], StringToDouble(FeedGet("gray_low")), InpClrAutoLevels, STYLE_DOT, 1, "AUTO เทา ล่าง " + Px(StringToDouble(FeedGet("gray_low"))));
      HLine(ids[3], StringToDouble(FeedGet("gray_high")), InpClrAutoLevels, STYLE_DOT, 1, "AUTO เทา บน " + Px(StringToDouble(FeedGet("gray_high"))));
      HLine(ids[4], StringToDouble(FeedGet("tp1")), InpClrAutoLevels, STYLE_DASHDOT, 2, "AUTO TP1 " + Px(StringToDouble(FeedGet("tp1"))));
     }
   else
      for(int i = 0; i < 5; i++)
         Remove(ids[i]);

   if(g_feed_n == 0 || FeedGet("symbol") != _Symbol)
      return;
   string calls[];
   int n = FeedAll("call", calls);
   long offset = ServerOffsetSec();
   for(int i = 0; i < n; i++)
     {
      string f[];
      if(StringSplit(calls[i], '|', f) < 6)
         continue;
      long t = StringToInteger(f[0]);
      string outcome = f[5];
      bool would_buy = (outcome == "dry_run" || outcome == "order");
      string tip = "engine " + f[1] + " · " + outcome + " · เข้า " + f[2] + " · หยุด " + f[3] + " · TP1 " + f[4];
      EngineMarker(f[0] + "_" + f[1], (datetime)(t + offset), StringToDouble(f[2]),
                   would_buy ? InpClrEngineBuy : InpClrEngineVeto, tip);
     }
  }

void EnginePanel()
  {
   Line("── Engine (Python) ──", InpClrMuted);
   if(g_feed_n == 0)
     {
      Line("ยังไม่เชื่อมต่อ — เปิด run.bat ทิ้งไว้ แล้ว engine จะส่งข้อมูลขึ้นกราฟเอง", InpClrWarn);
      return;
     }
   long age = FeedAgeSec();
   bool fresh = FeedFresh();
   string mode = FeedGet("mode");
   Line((fresh ? "เชื่อมต่อแล้ว" : "ขาดการติดต่อ") + " · " + mode + " · " + FeedGet("symbol") + " · ระดับ " + FeedGet("levels_source")
        + " · AI " + FeedGet("ai") + " · อัปเดต " + AgeText(age) + "ก่อน",
        !fresh ? InpClrBad : (mode == "LIVE" ? InpClrWarn : InpClrGood));
   if(FeedGet("symbol") != _Symbol)
      Line("engine ดู " + FeedGet("symbol") + " แต่กราฟนี้คือ " + _Symbol + " — ลูกศร engine จะไม่แสดง", InpClrWarn);
   if(FeedGet("levels_source") == "auto" && FeedGet("levels_ok") != "1")
      Line("ระดับอัตโนมัติ: วันนี้ไม่วาดเส้น (กรอบแคบ/ข้อมูลไม่พอ) → ไม่เทรดวันนี้", InpClrWarn);

   bool locked = FeedGet("zen_locked") == "1";
   long cooldown = StringToInteger(FeedGet("zen_cooldown"));
   string zen = "Zen: หลุดแผน " + FeedGet("zen_off_plan") + "/" + FeedGet("zen_max") + " · " + (locked ? "ล็อกวันนี้" : "ไม่ล็อก");
   if(cooldown > 0)
      zen += " · พักหลังขาดทุนอีก " + IntegerToString(cooldown) + " แท่ง";
   Line(zen, locked ? InpClrBad : InpClrText);

   long qn = StringToInteger(FeedGet("quant_n"));
   if(qn > 0)
      Line("Quant: n=" + IntegerToString(qn) + " · win " + FeedGet("quant_win") + "% · E[R] " + FeedGet("quant_er"),
           StringToDouble(FeedGet("quant_er")) > 0 ? InpClrGood : InpClrBad);
   else
      Line("Quant: n=0 — ยังไม่มีไม้ตามแผนที่ปิด (ห้ามสรุป)", InpClrMuted);

   string open_trade = FeedGet("open");
   if(StringLen(open_trade) > 0)
     {
      string f[];
      if(StringSplit(open_trade, '|', f) >= 5)
         Line("ไม้เปิด " + f[0] + " " + f[1] + " · เข้า " + f[2] + " · หยุด " + f[3] + " · TP1 " + f[4], InpClrGood);
     }

   string events[];
   int n = FeedAll("event", events);
   for(int i = IMax(0, n - InpFeedEvents); i < n; i++)
     {
      string f[];
      if(StringSplit(events[i], '|', f) < 3)
         continue;
      string text = ThaiClock(StringToInteger(f[0])) + " [" + f[1] + "] " + f[2];
      if(StringLen(text) > 78)
         text = StringSubstr(text, 0, 75) + "...";
      color c = InpClrMuted;
      if(f[1] == "dry_run" || f[1] == "order")
         c = InpClrGood;
      else
         if(f[1] == "omega" || f[1] == "risk_veto" || f[1] == "zen_block" || f[1] == "off_plan")
            c = InpClrWarn;
      Line(text, c);
     }
  }

void BuildPanel()
  {
   g_lines = 0;
   string ccy = AccountInfoString(ACCOUNT_CURRENCY);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   string vetoes = "";

   //--- header
   datetime h1_open = iTime(_Symbol, PERIOD_H1, 0);
   long left = (long)(h1_open + 3600 - TimeTradeServer());
   if(left < 0)
      left = 0;
   Line("// ANON :: NEURAL LINK · " + _Symbol + " · H1 ถัดไปปิดใน " + StringFormat("%02d:%02d", (int)(left / 60), (int)(left % 60)),
        InpClrText);
   Line("bid " + Px(bid) + " / ask " + Px(ask) + " · spread " + Px(ask - bid), InpClrMuted);
   if(g_n < 2)
     {
      Line("กำลังโหลดประวัติ H1 ...", InpClrWarn);
      RenderPanel();
      return;
     }

   double last_close = g_rates[g_n - 1].close;
   bool inside = (last_close >= GrayLow() && last_close <= GrayHigh());
   string where = inside ? "ในเทา → NO CHASE" : (last_close < GrayLow() ? "ใต้เทา" : "เหนือเทา");
   Line("H1 ปิดล่าสุด " + Px(last_close) + " (" + TimeToString(g_rates[g_n - 1].time, TIME_DATE | TIME_MINUTES)
        + ") · " + where, inside ? InpClrWarn : InpClrText);
   if(inside)
      vetoes += "เทา NO CHASE, ";

   //--- Ghost
   Line("── Ghost ──", InpClrMuted);
   double rr_now = 0.0;
   if(g_call.setup != 0)
     {
      rr_now = RewardRisk(ask, g_call.stop, InpTP1);
      Line("เรียก " + (g_call.setup == 1 ? "A/" : "B/") + g_call.variant + " · เข้า(ask) " + Px(ask) + " · หยุด "
           + Px(g_call.stop) + " · TP1 " + Px(InpTP1) + " · RR " + DoubleToString(rr_now, 2), InpClrGood);
      if(g_call.setup == 1)
         Line("thesis exit: ถ้า H1 ปิด < " + Px(InpAZoneBot) + " ให้ออก", InpClrMuted);
      if(rr_now < InpMinRR || ask >= InpTP1)
         vetoes += "RR หลัง spread ต่ำ, ";
     }
   else
     {
      Line("ไม่เรียก: " + g_call.reason, InpClrMuted);
      if(!inside)
         vetoes += "Ghost ยังไม่เรียก, ";
     }
   if(g_breakout >= 0)
      Line("B armed ตั้งแต่ " + TimeToString(g_rates[g_breakout].time, TIME_DATE | TIME_MINUTES) + " · รีเทส low ≤ "
           + Px(GrayHigh() + InpRetestTol) + " · swing stop " + Px(g_b_swing_stop) + " · เข้าได้ไม่เกิน "
           + Px(BMaxEntry(g_b_swing_stop)), InpClrText);
   else
      Line("B ยังไม่ armed (ต้องมี H1 ปิด > " + Px(GrayHigh()) + " แล้วรีเทส)", InpClrMuted);

   //--- Ω
   Line("── Ω ──", InpClrMuted);
   Line("Ω " + g_regime.regime + " → " + (g_regime.favorable ? "เอื้อ" : "ไม่เอื้อ") + " · " + g_regime.reason
        + " · EMA" + IntegerToString(InpEmaFast) + " " + Px(g_regime.ema_fast) + " / EMA" + IntegerToString(InpEmaSlow)
        + " " + Px(g_regime.ema_slow) + " · ATR " + Px(g_regime.atr),
        g_regime.favorable ? InpClrGood : InpClrBad);
   if(g_call.setup != 0 && !g_regime.favorable && InpOmegaVeto)
      vetoes += "Ω ไม่เอื้อ, ";

   //--- Risk
   Line("── Risk (lot " + DoubleToString(InpLot, 2) + ") ──", InpClrMuted);
   double pp = RiskMoney(ask + 1.0, ask);
   double cap_trade = equity * InpRiskPct / 100.0;
   string max_stop = (pp > 0.0) ? Px(cap_trade / pp) : "?";
   Line("equity " + Money(equity) + " " + ccy + " · 1 จุด = " + Money(pp) + " " + ccy + " · stop ไกลสุด "
        + max_stop + " จุด (≤" + DoubleToString(InpRiskPct, 1) + "%)", InpClrText);

   double trade_risk = 0.0;
   if(g_call.setup != 0)
     {
      trade_risk = RiskMoney(ask, g_call.stop);
      double pct = equity > 0 ? trade_risk / equity * 100.0 : 0.0;
      bool ok = trade_risk >= 0.0 && pct <= InpRiskPct + 1e-9;
      Line("ไม้ที่เรียก: เสี่ยง " + Money(trade_risk) + " " + ccy + " = " + DoubleToString(pct, 2) + "% → " + Verdict(ok),
           ok ? InpClrGood : InpClrBad);
      if(!ok)
         vetoes += "เสี่ยงเกิน " + DoubleToString(InpRiskPct, 1) + "%, ";
     }
   else
     {
      double spread = ask - bid;
      double a_entry = InpAZoneTop + spread;
      double a_stop = InpAZoneBot - InpStopBuffer;
      double a_risk = RiskMoney(a_entry, a_stop);
      double a_pct = equity > 0 ? a_risk / equity * 100.0 : 0.0;
      Line("A อ้างอิง (เข้า " + Px(a_entry) + " หยุด " + Px(a_stop) + "): เสี่ยง " + Money(a_risk) + " " + ccy + " = "
           + DoubleToString(a_pct, 2) + "% · RR " + DoubleToString(RewardRisk(a_entry, a_stop, InpTP1), 2),
           a_pct <= InpRiskPct ? InpClrMuted : InpClrWarn);
     }

   double day_start = DayStartBalance(balance);
   double cap_day = day_start * InpDailyPct / 100.0;
   double lost = MathMax(0.0, day_start - equity);
   bool day_ok = lost < cap_day && lost + trade_risk <= cap_day + 1e-9;
   Line("วันนี้ขาดทุน " + Money(lost) + " / เพดาน " + Money(cap_day) + " " + ccy + " (" + DoubleToString(InpDailyPct, 1)
        + "%) → " + Verdict(day_ok), day_ok ? InpClrText : InpClrBad);
   if(lost >= cap_day)
      vetoes += "ชนเพดานวัน, ";
   else
      if(!day_ok)
         vetoes += "ไม้นี้จะทะลุเพดานวัน, ";

   string gv = "ANON_PEAK_" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN));
   double peak = GlobalVariableCheck(gv) ? GlobalVariableGet(gv) : 0.0;
   peak = MathMax(peak, MathMax(equity, day_start));
   GlobalVariableSet(gv, peak);
   double dd = peak > 0 ? (peak - equity) / peak * 100.0 : 0.0;
   Line("peak " + Money(peak) + " · DD " + DoubleToString(dd, 2) + "% (เตือนที่ " + DoubleToString(InpPeakWarnPct, 1) + "%)",
        dd >= InpPeakWarnPct ? InpClrWarn : InpClrText);

   int shown = 0;
   int total = PositionsTotal();
   for(int i = 0; i < total; i++)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      long type = PositionGetInteger(POSITION_TYPE);
      long magic = PositionGetInteger(POSITION_MAGIC);
      double sl = PositionGetDouble(POSITION_SL);
      string side = (type == POSITION_TYPE_BUY) ? "buy" : "sell";
      string desc = "#" + IntegerToString((long)ticket) + " " + side + " " + DoubleToString(PositionGetDouble(POSITION_VOLUME), 2)
                    + " · P/L " + Money(PositionGetDouble(POSITION_PROFIT));
      color c = InpClrText;
      if((long)ticket == InpVetoTicket)
        {
         desc += " → VETO (หลุดแผน ห้ามนับ แต่ Risk นับ)";
         c = InpClrBad;
         vetoes += "VETO #" + IntegerToString(InpVetoTicket) + " เปิดอยู่, ";
        }
      else
         if(magic != InpBotMagic)
           {
            desc += " → ไม้นอกระบบ";
            c = InpClrBad;
            if(InpVetoUnmanaged)
               vetoes += "มีไม้นอกระบบ, ";
           }
         else
           {
            desc += " · ตามแผน SL " + (sl > 0 ? Px(sl) : "ไม่มี!");
            c = (sl > 0) ? InpClrGood : InpClrBad;
            vetoes += "มีไม้ตามแผนเปิดอยู่, ";
           }
      if(type == POSITION_TYPE_SELL)
         vetoes += "มี sell เปิด (ห้าม hedge), ";
      Line("ไม้เปิด " + desc, c);
      shown++;
     }
   if(shown == 0)
      Line("ไม่มีไม้เปิดบน " + _Symbol, InpClrMuted);
   if(InpLot > InpMaxLot)
      vetoes += "lot เกินเพดาน, ";

   //--- Zen
   Line("── Zen checklist ──", InpClrMuted);
   bool z1 = (g_call.setup != 0 && !inside);
   bool z2 = (g_call.setup != 0 && InpLot <= InpMaxLot && g_call.stop > 0 && trade_risk >= 0.0
              && equity > 0 && trade_risk / equity * 100.0 <= InpRiskPct + 1e-9);
   Line("(1) Ghost เรียกนอกเทา: " + Verdict(z1), z1 ? InpClrGood : InpClrBad);
   Line("(2) lot " + DoubleToString(InpLot, 2) + " + stop ตัวเลข + ไม่เกินเพดาน: " + Verdict(z2), z2 ? InpClrGood : InpClrBad);
   Line("(3) พร้อม #Txx / ตัวนับหลุดแผน / พักหลังขาดทุน: ดูใน engine", InpClrMuted);

   //--- verdict
   Line("── สรุป ──", InpClrMuted);
   if(StringLen(vetoes) > 0)
     {
      vetoes = StringSubstr(vetoes, 0, StringLen(vetoes) - 2);
      Line("ห้ามกด: " + vetoes, InpClrBad);
     }
   else
      Line("ผ่านเช็กฝั่งกราฟ → ให้ engine เช็ก Zen + รออนุมัติ", InpClrGood);
   if(InpShowEngine)
      EnginePanel();
   Line("ลูกศรใต้แท่ง = Ghost ของกราฟ · ป้าย Buy เขียว/ส้ม = engine จะกด/โดน veto · ไม่ส่งออเดอร์", InpClrMuted);
   RenderPanel();
  }

//+------------------------------------------------------------------+
//| indicator events                                                 |
//+------------------------------------------------------------------+
ENUM_CHART_PROPERTY_INTEGER g_theme_props[] = {CHART_COLOR_BACKGROUND, CHART_COLOR_FOREGROUND, CHART_COLOR_GRID,
   CHART_COLOR_CHART_UP, CHART_COLOR_CHART_DOWN, CHART_COLOR_CANDLE_BULL, CHART_COLOR_CANDLE_BEAR,
   CHART_COLOR_CHART_LINE, CHART_COLOR_BID, CHART_COLOR_ASK};
long g_theme_saved[10];
bool g_theme_on = false;

void CyberTheme()
  {
   color c[] = {C'5,2,15', C'150,120,210', C'30,10,50', C'0,240,255', C'255,0,170', C'0,240,255', C'255,0,170',
                C'0,240,255', C'255,230,0', C'255,0,170'};
   for(int i = 0; i < ArraySize(g_theme_props); i++)
     {
      g_theme_saved[i] = ChartGetInteger(0, g_theme_props[i]);
      ChartSetInteger(0, g_theme_props[i], c[i]);
     }
   g_theme_on = true;
  }

int OnInit()
  {
   if(InpCyberChart)
      CyberTheme();
   if(InpGrayHalf <= 0.0 || !(InpAZoneBot < InpAZoneTop && InpAZoneTop < GrayLow() && GrayHigh() < InpTP1))
     {
      Print("ANON: ระดับราคาต้องเรียง A bot < A top < เทา < TP1 และความกว้างเทา > 0");
      return INIT_PARAMETERS_INCORRECT;
     }
   if(InpMinRR <= 0.0 || InpMarkerBars < 0 || InpEmaFast < 1 || InpEmaSlow < 1 || InpAtrPeriod < 1)
     {
      Print("ANON: ค่า Ghost/Ω ไม่ถูกต้อง");
      return INIT_PARAMETERS_INCORRECT;
     }
   g_n = 0;
   g_last_h1 = 0;
   g_markers_for = 0;
   g_prev_lines = 0;
   g_breakout = -1;
   g_b_swing_stop = 0.0;
   g_call.setup = 0;
   g_call.reason = "no_data";
   g_regime.regime = "unknown";
   g_regime.favorable = false;
   SetIndexBuffer(0, BufEmaFast, INDICATOR_DATA);
   SetIndexBuffer(1, BufEmaSlow, INDICATOR_DATA);
   SetIndexBuffer(2, BufCallA, INDICATOR_DATA);
   SetIndexBuffer(3, BufCallB, INDICATOR_DATA);
   for(int i = 0; i < 4; i++)
      PlotIndexSetDouble(i, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   PlotIndexSetInteger(2, PLOT_ARROW, 233);
   PlotIndexSetInteger(3, PLOT_ARROW, 233);
   PlotIndexSetString(0, PLOT_LABEL, "Ω EMA" + IntegerToString(InpEmaFast));
   PlotIndexSetString(1, PLOT_LABEL, "Ω EMA" + IntegerToString(InpEmaSlow));
   IndicatorSetString(INDICATOR_SHORTNAME, "ANON_Levels_v2");
   IndicatorSetInteger(INDICATOR_DIGITS, _Digits);
   if(InpShowPanel)
      PanelBg(1);
   EventSetTimer(1);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   if(g_theme_on && reason != REASON_CHARTCHANGE)
      for(int i = 0; i < ArraySize(g_theme_props); i++)
         ChartSetInteger(0, g_theme_props[i], g_theme_saved[i]);
   ObjectsDeleteAll(0, PFX);
   ChartRedraw();
  }

void OnTimer()
  {
   CheckNewH1();
   DrawLevels();
   if(InpShowEngine)
     {
      ReadFeed();
      DrawEngine();
     }
   if(InpShowPanel)
      BuildPanel();
   ChartRedraw();
  }

int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &time[],
                const double &open[],
                const double &high[],
                const double &low[],
                const double &close[],
                const long &tick_volume[],
                const long &volume[],
                const int &spread[])
  {
   if(rates_total < 2)
      return 0;
   bool fresh = CheckNewH1() || prev_calculated == 0;
   bool is_h1 = (_Period == PERIOD_H1);
   double kf = 2.0 / (InpEmaFast + 1);
   double ks = 2.0 / (InpEmaSlow + 1);
   int start = (prev_calculated == 0) ? 0 : prev_calculated - 1;
   for(int i = start; i < rates_total; i++)
     {
      BufCallA[i] = EMPTY_VALUE;
      BufCallB[i] = EMPTY_VALUE;
      if(!is_h1)
        {
         BufEmaFast[i] = EMPTY_VALUE;
         BufEmaSlow[i] = EMPTY_VALUE;
         continue;
        }
      if(i == 0)
        {
         BufEmaFast[i] = close[i];
         BufEmaSlow[i] = close[i];
        }
      else
        {
         BufEmaFast[i] = BufEmaFast[i - 1] + kf * (close[i] - BufEmaFast[i - 1]);
         BufEmaSlow[i] = BufEmaSlow[i - 1] + ks * (close[i] - BufEmaSlow[i - 1]);
        }
     }

   if(is_h1 && g_n >= 2 && (fresh || g_markers_for != g_last_h1))
     {
      g_markers_for = g_last_h1;
      GhostCall c;
      int first = IMax(0, g_n - InpMarkerBars);
      for(int k = first; k < g_n; k++)
        {
         GhostEvaluate(g_rates, k, c);
         if(c.setup == 0)
            continue;
         int shift = iBarShift(_Symbol, PERIOD_H1, g_rates[k].time, true);
         if(shift < 0)
            continue;
         int idx = rates_total - 1 - shift;
         if(idx < 0 || idx >= rates_total)
            continue;
         double offset = (high[idx] - low[idx]) * 0.5 + 10 * _Point;
         if(c.setup == 1)
            BufCallA[idx] = low[idx] - offset;
         else
            BufCallB[idx] = low[idx] - offset;
        }
     }
   return rates_total;
  }
//+------------------------------------------------------------------+
