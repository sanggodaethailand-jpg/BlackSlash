# BlackSlash — ระบบเทรด ANON (BTCUSD H1)

ระบบนี้แปลงใบส่งมอบห้อง ANON ให้เป็นโค้ด ทุกไม้ต้องผ่านลำดับเดียวกับที่ใบส่งมอบเขียนไว้:

```
แท่ง H1 ปิด → Ghost เรียก → Ω แท็ก → Risk เช็กเพดาน → Zen เช็ก checklist → อนุมัติ → กด 0.01 → Quant แปะ #Txx
```

ถ้าด่านไหนไม่ผ่าน ระบบจะไม่กด และบันทึกเหตุผลไว้ทุกครั้ง

> ⚠️ **ระบบนี้ไม่รับประกันกำไร** ข้อมูลสังเคราะห์และเทสต์ในนี้พิสูจน์ได้แค่ว่า *กติกาทำงานถูก* ยังพิสูจน์ไม่ได้ว่าได้เปรียบในตลาดจริง
> ทางที่เร็วที่สุดที่ไปได้จริงคือ **เก็บหลักฐานให้เร็วที่สุด** (backtest → dry-run → เงินจริงแบบอนุมัติมือ) ไม่ใช่เร่ง lot
> ตัวอย่าง: 10,000 บาท/วัน ที่ lot 0.01 ต้องได้ราว 30,000 จุดต่อวัน (`anon math`) เป้านี้จึงถูก VETO

## บทบาท → โค้ด

| บทบาท | ไฟล์ | สิ่งที่บังคับ |
|---|---|---|
| Ghost | `anon/ghost.py` | เรียก A (โซน 83000–83500 หรือ sweep-and-reclaim) และ B (รีเทสหลังปิดเหนือเทา) · ไม่เรียกเมื่อแท่งปิดในเทา · stop เป็นตัวเลขเสมอ |
| Ω | `anon/omega.py`, `anon/omega_ai.py` | แท็ก regime ด้วย EMA/ATR · Claude (ถ้าเปิด) **veto ได้อย่างเดียว** และถ้า AI ล่ม ถือว่า "ไม่เอื้อ" |
| Risk | `anon/risk.py` | lot 0.01 · ≤0.5%/ไม้ · ≤2%/วัน (นับ**ทุก**ไม้รวมไม้หลุดแผน) · เตือน peak 5% · VETO #208190412 / ไม้นอกระบบ / hedge |
| Zen | `anon/zen.py` | checklist 3 ข้อ · หลุดแผน 2 ครั้งติด = ล็อกวัน · พักหลังขาดทุน · stop ที่ถูกขยายจะถูกดึงกลับ |
| อนุมัติ | `anon/approval.py` | ต้องพิมพ์ `อนุมัติ #Txx` ตรงตัว · ก่อนส่งออเดอร์จะเช็กราคาใหม่อีกรอบ |
| Quant | `anon/quant.py` | บันทึก `#Txx` · R วัดจากราคาเข้าจริงถึง stop · E[R] เฉพาะไม้ตามแผน แยกตาม Ω · บาทมาจาก P/L จริงเท่านั้น |
| Broker | `anon/broker/paper.py`, `anon/broker/mt5.py` | SL/TP อยู่ฝั่งโบรกเกอร์เสมอ ถ้าโปรแกรมดับ ไม้ที่เปิดอยู่ก็ยังมี SL/TP · MT5 เป็น dry-run โดยค่าเริ่มต้น |

## ช่องโหว่ในใบส่งมอบที่ปิดแล้ว

1. **A ใช้ sweep-and-reclaim แต่ inv อยู่ที่ 83000** → hard stop อยู่ใต้ low ของ sweep ลบ buffer ส่วน "H1 ปิดต่ำกว่า 83000" ใช้เป็น thesis exit แยกต่างหาก
2. **เทาไม่มีความกว้าง** → กำหนดเป็นแถบ `gray_center ± gray_half_width` (ค่าเริ่มต้น 84550–84850 **ต้องยืนยัน**)
3. **#208190412 "ห้ามนับ"** → Quant ไม่นับไม้นี้ แต่ Risk ยังนับเข้าเพดานรายวัน และห้ามเปิดไม้ใหม่ระหว่างที่ไม้นี้ยังเปิดอยู่
4. **Ω "ไม่เอื้อ" = veto หรือแค่แท็ก** → ตั้งได้ใน `omega.unfavorable_action` (ค่าเริ่มต้น `veto`)

## ติดตั้ง (Windows + MT5)

```powershell
py -3.11 -m venv .venv; .venv\Scripts\activate
pip install -e ".[mt5,ai,dev]"
copy config\anon.example.toml config\anon.toml   # แล้วแก้ค่าที่เขียนว่า "ต้องยืนยัน"
$env:MT5_LOGIN="..."; $env:MT5_PASSWORD="..."; $env:MT5_SERVER="..."   # ห้ามใส่รหัสผ่านใน config
$env:ANTHROPIC_API_KEY="..."   # เฉพาะเมื่อ omega.ai_enabled = true
```

## คำสั่ง

```bash
anon math --equity 1000 --usdthb 33.42          # พิสูจน์ตัวเลข Risk จาก config
anon demo                                        # ดูทุกด่านทำงานบนข้อมูลสังเคราะห์
anon --config config/anon.toml backtest --csv data/BTCUSD_H1.csv --events
anon report --journal journal/anon_journal.jsonl # ฟอร์ม #Txx + สถิติ
anon --config config/anon.toml live              # MT5 dry-run: คำนวณทุกอย่าง แต่ไม่ส่งออเดอร์
anon --config config/anon.toml live --confirm-live   # ส่งจริง: ต้องตั้ง dry_run=false ด้วย
```

ไฟล์ CSV: ใน MT5 เปิด View → Symbols → Bars เลือก BTCUSD H1 แล้วกด Export ได้เลย โปรแกรมอ่านรูปแบบนี้ได้ตรง ๆ

## ด่านก่อนใช้เงินจริง

| ด่าน | ผ่านเมื่อ |
|---|---|
| 1. `anon math` | เจ้าของยืนยันค่าที่ "ต้องยืนยัน" ครบ และขนาดพอร์ตรองรับ 0.5%/ไม้ได้ |
| 2. backtest ข้อมูลจริง | มีไม้ตามแผน ≥ 30 ไม้ และ E[R] > 0 (ถ้ายังไม่ถึง ห้ามสรุป) |
| 3. `live` dry-run บนบัญชี demo | log ตรงกับที่ Ghost ควรเรียก โดยไม่มี error อย่างน้อย 1–2 สัปดาห์ |
| 4. `--confirm-live` + `approval = "manual"` | ทุกไม้ต้องมีคนพิมพ์อนุมัติ |
| 5. `approval = "auto"` | ระบบจะไม่ยอมเริ่มจนกว่า Quant ผ่าน: n ≥ 30, E[R] > 0, P(edge>0) ≥ 0.9 |

## ข้อจำกัดที่รู้อยู่

- มีแค่ฝั่ง Buy เพราะใบส่งมอบมีแค่ A/B ฝั่งซื้อ
- backtest ใช้ spread คงที่ ไม่คิด swap/commission และความละเอียดเวลาอยู่ที่ 1 แท่ง H1 (ถ้า SL กับ TP โดนในแท่งเดียวกัน จะนับ SL ก่อน)
- MT5 adapter ทดสอบกับ MT5 ปลอมเท่านั้น ต้องลองรันบนบัญชี demo ก่อนเสมอ
- ขนาดสัญญา (contract size) ต้องเช็กจากหน้า Specification ของโบรกเกอร์ ตอนรัน live ระบบจะอ่านค่าจากโบรกเกอร์เอง

## Test

```bash
pip install -e ".[dev]" && pytest
```
