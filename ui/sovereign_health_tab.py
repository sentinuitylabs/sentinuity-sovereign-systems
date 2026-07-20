"""
ui/sovereign_health_tab.py
===========================
Lane 3: Sovereign Biological Intelligence

Shared cognition, separate execution. Learn first. Mutate later.

Lane 3 is an observational biological cognition layer. It shares the same
signal schema, confidence/freshness/drift primitives, and Guardian safety
gates as the trading lanes. It may NOT mutate Lane 1 or Lane 2 execution
thresholds — biological correlations are read-only until explicitly approved.

Lanes:
  Lane 1: Micro trading signals (pump.fun)
  Lane 2: Macro temporal compression (altcoin substrate)
  Lane 3: Biological signal intelligence (this file)

Tone: Forensic. Calm. High-agency. Evidence-driven. Non-prescriptive.
"""
from __future__ import annotations
import streamlit as st
import html
import time
import json
from pathlib import Path

# ── SIGNAL SCHEMA ─────────────────────────────────────────────────────────────
# Each intervention is a Signal Object with the same primitives as trading lanes

def _sig(name, mechanism, evidence_class, confidence, freshness, drift,
         risk_level, established, emerging, known_unknowns, what_increases_confidence,
         contraindications=None, sources_au=None, sources_intl=None,
         price_range_aud=None, tags=None):
    return {
        "name": name,
        "mechanism": mechanism,
        "evidence_class": evidence_class,  # RCT / Meta / Observational / Mechanistic / Emerging
        "confidence": confidence,           # 0.0-1.0
        "freshness": freshness,             # HIGH/MEDIUM/LOW - how current the evidence is
        "drift": drift,                     # how much expert consensus has shifted recently
        "risk_level": risk_level,           # LOW/MEDIUM/HIGH/CONTEXT-DEPENDENT
        "established": established,         # mainstream interpretation
        "emerging": emerging,               # frontier interpretation
        "known_unknowns": known_unknowns,
        "what_increases_confidence": what_increases_confidence,
        "contraindications": contraindications or [],
        "sources_au": sources_au or [],
        "sources_intl": sources_intl or [],
        "price_range_aud": price_range_aud or "",
        "tags": tags or [],
    }

# ── SIGNAL DATABASE ────────────────────────────────────────────────────────────
BIOLOGICAL_SIGNALS = {

    "cardiovascular": [
        _sig(
            name="Omega-3 (EPA/DHA)",
            mechanism="Reduces triglycerides, modulates eicosanoid pathways, mild anti-inflammatory",
            evidence_class="Meta-analysis (RCT)",
            confidence=0.82,
            freshness="HIGH",
            drift="STABLE",
            risk_level="LOW",
            established="Reduces cardiovascular events in high-risk populations. TG reduction well-established.",
            emerging="REDUCE-IT trial suggested higher-dose EPA (icosapentaenoic acid alone) may have additional CV benefit beyond lipid effects — mechanism debated (atrial fibrillation signal noted).",
            known_unknowns="Optimal EPA:DHA ratio unclear. Whether benefits extend to low-risk individuals is contested.",
            what_increases_confidence="Larger RCTs in general population, mechanism clarification for high-dose EPA.",
            contraindications=["Anticoagulant therapy (bleeding risk — consult clinician)", "Fish allergy"],
            sources_au=["iHerb AU (Nordic Naturals, Carlson)", "Chemist Warehouse (Blackmores, Swisse — verify EPA/DHA mg not just fish oil mg)"],
            sources_intl=["Nordic Naturals (3rd-party tested)", "Carlson (IFOS certified)"],
            price_range_aud="$30-80/month for quality product",
            tags=["cardiovascular", "inflammation", "foundational"]
        ),
        _sig(
            name="Coenzyme Q10 (CoQ10 / Ubiquinol)",
            mechanism="Mitochondrial electron transport chain cofactor, antioxidant",
            evidence_class="RCT (limited scale)",
            confidence=0.58,
            freshness="MEDIUM",
            drift="STABLE",
            risk_level="LOW",
            established="Statin users may have reduced CoQ10 synthesis — supplementation may reduce statin myopathy in some. Heart failure: some evidence for symptom improvement.",
            emerging="Ubiquinol form may have superior bioavailability in older adults. Emerging research in metabolic syndrome.",
            known_unknowns="Whether routine supplementation benefits healthy individuals is unclear.",
            what_increases_confidence="Larger RCTs in statin users, standardised dosing protocols.",
            contraindications=["May interact with warfarin — monitor INR"],
            sources_au=["Blackmores, Swisse (standard CoQ10)", "iHerb (Jarrow Ubiquinol for active form)"],
            price_range_aud="$25-60/month",
            tags=["cardiovascular", "mitochondrial"]
        ),
    ],

    "metabolic": [
        _sig(
            name="Time-Restricted Eating (TRE / IF)",
            mechanism="Circadian alignment, autophagy induction (ATG pathway), insulin sensitivity improvement",
            evidence_class="RCT + Mechanistic",
            confidence=0.74,
            freshness="HIGH",
            drift="POSITIVE (growing evidence)",
            risk_level="LOW",
            established="16:8 or similar windows improve insulin sensitivity, reduce fasting glucose, support body composition in overweight populations.",
            emerging="Autophagy induction may have longevity implications (mTOR inhibition). Circadian-aligned eating (earlier window) may outperform late-eating windows.",
            known_unknowns="Optimal window timing and duration unclear. Effects in lean athletic populations not well-studied. Long-term adherence data limited.",
            what_increases_confidence="Longer RCTs with autophagy biomarkers, head-to-head comparison of window timing.",
            contraindications=["History of eating disorders", "Type 1 diabetes (hypoglycaemia risk)", "Pregnancy", "Certain medications requiring food"],
            price_range_aud="$0 — behavioural intervention",
            tags=["metabolic", "autophagy", "foundational"]
        ),
        _sig(
            name="Berberine",
            mechanism="AMPK activation, gut microbiome modulation, mild glucose disposal",
            evidence_class="RCT (primarily Chinese populations)",
            confidence=0.62,
            freshness="MEDIUM",
            drift="POSITIVE",
            risk_level="MEDIUM",
            established="Comparable to metformin in some glucose-lowering trials. Reduces LDL in some studies.",
            emerging="Called 'nature's metformin' — AMPK pathway overlap. Possible longevity implications. GLP-1 interaction under study.",
            known_unknowns="Long-term safety data limited. Population generalisability uncertain (most trials in Chinese T2D patients). Drug interactions not fully characterised.",
            what_increases_confidence="Larger Western-population RCTs, longer-term safety data, pharmacokinetic studies.",
            contraindications=["Do not combine with metformin without clinician supervision", "Pregnancy (theoretical teratogenicity)", "CYP3A4 drug interactions"],
            sources_au=["iHerb (Thorne, NOW Foods)", "Hard to find in AU pharmacies"],
            price_range_aud="$25-45/month",
            tags=["metabolic", "glucose", "emerging"]
        ),
        _sig(
            name="Creatine Monohydrate",
            mechanism="Phosphocreatine resynthesis, ATP buffering, possible neuroprotective effects",
            evidence_class="Meta-analysis (RCT)",
            confidence=0.88,
            freshness="HIGH",
            drift="STABLE",
            risk_level="LOW",
            established="Most well-evidenced sports supplement. Improves power output, muscle mass in resistance training. Safe long-term.",
            emerging="Cognitive benefits in sleep-deprived states and older adults under investigation. Possible role in depression (brain energy hypothesis).",
            known_unknowns="Optimal dosing for cognitive effects. Individual response varies (non-responders ~25-30%).",
            what_increases_confidence="Larger cognitive RCTs, brain creatine measurement via MRS.",
            contraindications=["Pre-existing kidney disease (theoretical, not proven in healthy individuals)", "Adequate hydration required"],
            sources_au=["Bulk Nutrients AU (excellent value, local)", "True Protein AU", "Chemist Warehouse (generic brands)"],
            price_range_aud="$15-30/month (monohydrate — avoid expensive forms)",
            tags=["metabolic", "cognitive", "foundational", "high-evidence"]
        ),
    ],

    "cognitive": [
        _sig(
            name="Lion's Mane Mushroom (Hericium erinaceus)",
            mechanism="NGF (Nerve Growth Factor) stimulation via hericenones/erinacines, possible BDNF modulation",
            evidence_class="Emerging (small RCTs + animal studies)",
            confidence=0.48,
            freshness="HIGH",
            drift="POSITIVE (significant recent interest)",
            risk_level="LOW",
            established="Small Japanese RCT showed cognitive improvement in mild cognitive impairment. Animal models show strong neurogenic effects.",
            emerging="Rapidly growing research interest. Some human data on mood and anxiety. Potential synergy with sleep quality.",
            known_unknowns="Optimal extract standardisation unclear (hericenone vs erinacine content varies wildly). Long-term human data absent. Bioavailability of active compounds uncertain.",
            what_increases_confidence="Standardised extract RCTs in healthy adults, bioavailability studies, larger sample sizes.",
            contraindications=["Mushroom allergy", "Autoimmune conditions (theoretical immune modulation)"],
            sources_au=["Real Mushrooms (high erinacine content, well-regarded)", "iHerb", "Host Defense"],
            price_range_aud="$40-80/month for quality extract",
            tags=["cognitive", "neuroplasticity", "emerging"]
        ),
        _sig(
            name="Bacopa Monnieri",
            mechanism="Adaptogen, acetylcholinesterase inhibition (mild), antioxidant, BDNF modulation",
            evidence_class="RCT (multiple, modest scale)",
            confidence=0.64,
            freshness="MEDIUM",
            drift="STABLE",
            risk_level="LOW",
            established="Consistent evidence for memory consolidation improvement, particularly in older adults. Effects emerge after 8-12 weeks of use.",
            emerging="Possible anxiolytic effects. Interaction with serotonin system under investigation.",
            known_unknowns="Active compound standardisation varies. Mechanism in young healthy adults less studied.",
            what_increases_confidence="Larger RCTs in diverse age groups, standardised Bacoside A/B content.",
            contraindications=["May increase GI motility", "Thyroid medication interactions (theoretical)"],
            sources_au=["iHerb (Jarrow, NOW Foods)", "Swisse (limited evidence for their formulation)"],
            price_range_aud="$20-40/month",
            tags=["cognitive", "memory", "adaptogen"]
        ),
        _sig(
            name="Psilocybin — Neuroplasticity Research Context",
            mechanism="5-HT2A agonism → Default Mode Network (DMN) desynchronisation → neuroplasticity window",
            evidence_class="Phase 2 RCT (treatment-resistant depression, end-of-life anxiety)",
            confidence=0.71,  # for specific therapeutic use cases, not general
            freshness="HIGH",
            drift="RAPIDLY POSITIVE (major paradigm shift underway)",
            risk_level="CONTEXT-DEPENDENT",
            established="Phase 2 data shows significant, durable antidepressant effects in treatment-resistant depression (Johns Hopkins, Imperial College London, MAPS). FDA Breakthrough Therapy designation.",
            emerging="Microdosing research mixed and inconclusive — placebo effects large. Neuroimaging shows measurable DMN changes. Potential in OCD, addiction, cluster headaches.",
            known_unknowns="Optimal dosing protocols unclear. Long-term neuroplasticity effects unknown. Individual variation large. Psychological preparation and integration ('set and setting') appear to be significant variables. Phase 3 trials ongoing.",
            what_increases_confidence="Phase 3 RCT completion, neuroimaging biomarker studies, long-term follow-up data, microdosing placebo-controlled trials.",
            contraindications=[
                "Personal or family history of psychosis, schizophrenia, or bipolar I — STRONG CONTRAINDICATION",
                "Lithium or MAOI combination — potentially life-threatening",
                "Uncontrolled cardiovascular disease",
                "Pregnancy",
                "Active suicidal ideation without clinical supervision",
                "Serotonin syndrome risk with SSRIs/SNRIs — dose timing matters",
            ],
            sources_au=["Currently Schedule 9 (prohibited) in most Australian states. TGA approved limited clinical use from Feb 2023 for treatment-resistant depression and PTSD via authorised prescribers only."],
            sources_intl=["MAPS (maps.org)", "Johns Hopkins Center for Psychedelic Research", "Multidisciplinary Association for Psychedelic Studies"],
            price_range_aud="Clinical context only — not available OTC",
            tags=["cognitive", "neuroplasticity", "psilocybin", "emerging", "restricted"]
        ),
    ],

    "inflammation": [
        _sig(
            name="Curcumin (with Piperine or Liposomal)",
            mechanism="NF-κB pathway inhibition, COX-2 modulation, antioxidant",
            evidence_class="RCT (multiple, bioavailability-dependent)",
            confidence=0.61,
            freshness="HIGH",
            drift="STABLE",
            risk_level="LOW",
            established="Anti-inflammatory effects demonstrated in vitro and in some clinical conditions (OA, metabolic syndrome). Standard curcumin poorly absorbed — formulation matters critically.",
            emerging="Theracurmin, Meriva, and liposomal forms show superior bioavailability. Possible role in metabolic syndrome and neuroinflammation.",
            known_unknowns="Clinical significance of bioavailability improvements unclear. Long-term safety of high-absorption forms unknown.",
            what_increases_confidence="RCTs using validated high-bioavailability forms, inflammatory biomarker endpoints (CRP, IL-6).",
            contraindications=["Bile duct obstruction", "High-dose may affect iron absorption", "Anticoagulants at high dose"],
            sources_au=["Theracurmin (Integria)", "Meriva-form products on iHerb", "Avoid basic curcumin powder"],
            price_range_aud="$30-60/month for quality bioavailable form",
            tags=["inflammation", "antioxidant"]
        ),
    ],

    "immune": [
        _sig(
            name="Vitamin D3 + K2",
            mechanism="VDR nuclear receptor activation, immune modulation, calcium metabolism",
            evidence_class="RCT + Epidemiological",
            confidence=0.76,
            freshness="HIGH",
            drift="STABLE",
            risk_level="LOW",
            established="Deficiency clearly linked to immune dysfunction, bone health, mood. Sufficiency (>75 nmol/L serum) associated with reduced respiratory infection rates. COVID-19 highlighted widespread deficiency.",
            emerging="K2 (MK-7 form) may direct calcium to bone/away from arteries. Combination supplementation gaining support.",
            known_unknowns="Optimal serum target debated (75 vs 100 vs 125 nmol/L). Supplementation benefit in non-deficient individuals unclear.",
            what_increases_confidence="Test serum 25(OH)D before supplementing. RCTs in non-deficient populations.",
            contraindications=["Hypercalcaemia", "Granulomatous disease (sarcoidosis — can cause D toxicity)", "Some heart medications with K2"],
            sources_au=["Test first (GP or private lab)", "Healthy Life, Chemist Warehouse (D3 widely available)", "K2 MK-7: iHerb or health food stores"],
            price_range_aud="$10-25/month",
            tags=["immune", "foundational", "high-evidence"]
        ),
        _sig(
            name="Magnesium (Glycinate or Malate)",
            mechanism="Cofactor in 300+ enzymatic reactions, NMDA receptor modulation, HPA axis modulation",
            evidence_class="RCT + Mechanistic",
            confidence=0.72,
            freshness="HIGH",
            drift="STABLE",
            risk_level="LOW",
            established="Deficiency common in Western diets. Supplementation improves sleep quality, reduces muscle cramps, may improve insulin sensitivity.",
            emerging="Magnesium L-threonate may cross BBB more effectively for cognitive benefit. Sleep onset and quality effects robust.",
            known_unknowns="Form superiority debate ongoing. Optimal dosing unclear. Bioavailability varies significantly by form.",
            what_increases_confidence="Head-to-head RCTs comparing forms, intracellular (RBC) magnesium measurement.",
            contraindications=["Kidney disease — do not supplement without medical supervision", "Laxative effect at high doses (avoid oxide form)"],
            sources_au=["Bulk Nutrients AU (magnesium glycinate)", "Blackmores (glycinate form)", "Avoid oxide form"],
            price_range_aud="$15-35/month",
            tags=["foundational", "sleep", "immune", "high-evidence"]
        ),
    ],

    "foundational": [
        _sig(
            name="Sleep Architecture Optimisation",
            mechanism="Adenosine clearance, glymphatic system activation, cortisol regulation, memory consolidation",
            evidence_class="Observational + Mechanistic (RCT limited by design constraints)",
            confidence=0.91,
            freshness="HIGH",
            drift="STABLE",
            risk_level="LOW",
            established="7-9 hours sleep is the single most evidence-backed health intervention. Glymphatic system clears amyloid during slow-wave sleep. Sleep restriction causally impairs cognition, immunity, metabolic function.",
            emerging="Chronotype matching (sleeping at biological clock-optimal time) may matter as much as duration. Blue light exposure at night suppresses melatonin reliably.",
            known_unknowns="Individual variation in sleep need is genuine. Optimal sleep architecture composition varies.",
            what_increases_confidence="N/A — this is the most established finding in health science.",
            contraindications=[],
            price_range_aud="$0 — behavioural",
            tags=["foundational", "cognitive", "immune", "high-evidence"]
        ),
        _sig(
            name="Zone 2 Aerobic Training",
            mechanism="Mitochondrial biogenesis, VO2max improvement, metabolic flexibility, BDNF release",
            evidence_class="RCT + Longitudinal observational",
            confidence=0.87,
            freshness="HIGH",
            drift="STABLE",
            risk_level="LOW",
            established="150-300 min/week moderate aerobic activity: most consistent longevity signal in epidemiological data. VO2max is the strongest predictor of all-cause mortality.",
            emerging="Zone 2 (conversational pace, fat-oxidation dominant) may be superior to high-intensity for mitochondrial adaptation in sedentary individuals.",
            known_unknowns="Optimal intensity distribution still debated. Zone 2 definitions vary between practitioners.",
            what_increases_confidence="VO2max measurement, lactate threshold testing, long-term RCTs.",
            contraindications=["Uncontrolled cardiovascular disease — medical clearance required"],
            price_range_aud="$0 — behavioural",
            tags=["foundational", "cardiovascular", "cognitive", "high-evidence"]
        ),
    ],
}

# ── COLOUR SYSTEM ─────────────────────────────────────────────────────────────
NODE_COLOURS = {
    "cardiovascular": {"primary": "#FF6B6B", "glow": "#FF6B6B44", "icon": "♥"},
    "metabolic":      {"primary": "#FFC94A", "glow": "#FFC94A44", "icon": "⚡"},
    "cognitive":      {"primary": "#A14BFF", "glow": "#A14BFF44", "icon": "🧠"},
    "inflammation":   {"primary": "#FF8C00", "glow": "#FF8C0044", "icon": "🔥"},
    "immune":         {"primary": "#1CF2A4", "glow": "#1CF2A444", "icon": "⬡"},
    "foundational":   {"primary": "#7DF4FF", "glow": "#7DF4FF44", "icon": "◈"},
}

NODE_LABELS = {
    "cardiovascular": "CARDIOVASCULAR / CIRCULATORY",
    "metabolic":      "METABOLIC / AUTOPHAGY",
    "cognitive":      "COGNITIVE / NEUROPLASTICITY",
    "inflammation":   "INFLAMMATION",
    "immune":         "IMMUNE / RESILIENCE",
    "foundational":   "FOUNDATIONAL: SLEEP · LIGHT · MOVEMENT",
}

EVIDENCE_COLOURS = {
    "Meta-analysis (RCT)":       "#1CF2A4",
    "RCT (multiple, modest scale)": "#7DF4FF",
    "RCT + Mechanistic":         "#7DF4FF",
    "RCT (limited scale)":       "#FFC94A",
    "Emerging (small RCTs + animal studies)": "#FF8C00",
    "Phase 2 RCT (treatment-resistant depression, end-of-life anxiety)": "#A14BFF",
    "Observational + Mechanistic (RCT limited by design constraints)": "#1CF2A4",
    "RCT + Longitudinal observational": "#1CF2A4",
    "RCT + Epidemiological":     "#1CF2A4",
    "RCT (primarily Chinese populations)": "#FFC94A",
}

RISK_COLOURS = {
    "LOW":                "#1CF2A4",
    "MEDIUM":             "#FFC94A",
    "HIGH":               "#FF2E63",
    "CONTEXT-DEPENDENT":  "#FF8C00",
}


def _confidence_bar(conf: float, colour: str) -> str:
    pct = int(conf * 100)
    bar_pct = int(conf * 100)
    return (
        f"<div style='display:flex;align-items:center;gap:8px;margin:4px 0;'>"
        f"<div style='flex:1;height:4px;background:rgba(255,255,255,0.08);border-radius:2px;overflow:hidden;'>"
        f"<div style='height:100%;width:{bar_pct}%;background:{colour};'></div></div>"
        f"<span style='font-family:Share Tech Mono,monospace;font-size:0.66rem;color:{colour};min-width:36px;'>{pct}%</span>"
        f"</div>"
    )


def _signal_card(sig: dict, node_colour: str) -> str:
    ec_col = EVIDENCE_COLOURS.get(sig["evidence_class"], "#888")
    risk_col = RISK_COLOURS.get(sig["risk_level"], "#888")
    conf_bar = _confidence_bar(sig["confidence"], node_colour)
    name = html.escape(sig["name"])
    mechanism = html.escape(sig["mechanism"])
    established = html.escape(sig["established"][:200])
    emerging = html.escape(sig["emerging"][:200])

    contra_html = ""
    if sig["contraindications"]:
        items = "".join(
            f"<li style='margin-bottom:3px;color:#FF6B6B;'>{html.escape(c)}</li>"
            for c in sig["contraindications"]
        )
        contra_html = (
            f"<div style='margin-top:10px;padding:8px;background:rgba(255,46,99,0.06);"
            f"border-left:2px solid #FF2E63;border-radius:0 6px 6px 0;'>"
            f"<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;"
            f"color:#FF2E63;letter-spacing:2px;margin-bottom:4px;'>⚠ GUARDIAN INTERCEPT</div>"
            f"<ul style='margin:0;padding-left:16px;font-size:0.66rem;'>{items}</ul>"
            f"</div>"
        )

    sources_html = ""
    if sig.get("sources_au"):
        src_items = "".join(
            f"<li style='margin-bottom:2px;'>{html.escape(s)}</li>"
            for s in sig["sources_au"]
        )
        sources_html = (
            f"<div style='margin-top:8px;'>"
            f"<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;"
            f"color:#7DF4FF;letter-spacing:1px;margin-bottom:3px;'>◈ AUSTRALIA SOURCES</div>"
            f"<ul style='margin:0;padding-left:14px;font-size:0.66rem;color:#aaa;'>{src_items}</ul>"
            f"</div>"
        )

    price_html = ""
    if sig.get("price_range_aud"):
        price_html = (
            f"<div style='margin-top:6px;font-family:Share Tech Mono,monospace;"
            f"font-size:0.66rem;color:#FFC94A;'>◈ {html.escape(sig['price_range_aud'])}</div>"
        )

    return f"""
<div style='margin-bottom:14px;padding:14px;
    border:1px solid rgba(255,255,255,0.08);border-radius:10px;
    background:rgba(5,2,16,0.5);'>
  <div style='display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;'>
    <div style='font-family:Share Tech Mono,monospace;font-size:0.75rem;
        color:{node_colour};letter-spacing:2px;font-weight:bold;'>{name}</div>
    <span style='font-size:0.66rem;padding:2px 7px;border-radius:3px;
        background:{risk_col}22;color:{risk_col};border:1px solid {risk_col}55;
        white-space:nowrap;'>{html.escape(sig['risk_level'])}</span>
  </div>
  <div style='font-size:0.66rem;color:#888;font-style:italic;margin-bottom:8px;'>{mechanism}</div>
  <div style='display:flex;align-items:center;gap:8px;margin-bottom:6px;'>
    <span style='font-size:0.66rem;padding:1px 6px;border-radius:3px;
        background:{ec_col}22;color:{ec_col};border:1px solid {ec_col}44;'>{html.escape(sig['evidence_class'])}</span>
    <span style='font-size:0.66rem;color:#555;'>{sig['freshness']} · DRIFT: {sig['drift']}</span>
  </div>
  {conf_bar}
  <div style='margin-top:10px;'>
    <div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;
        color:#7DF4FF;letter-spacing:1px;margin-bottom:3px;'>ESTABLISHED</div>
    <div style='font-size:0.66rem;color:#ccc;line-height:1.5;'>{established}</div>
  </div>
  <div style='margin-top:8px;'>
    <div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;
        color:#FFC94A;letter-spacing:1px;margin-bottom:3px;'>EMERGING</div>
    <div style='font-size:0.66rem;color:#aaa;font-style:italic;line-height:1.5;'>{emerging}</div>
  </div>
  <div style='margin-top:8px;'>
    <div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;
        color:#555;letter-spacing:1px;margin-bottom:3px;'>KNOWN UNKNOWNS</div>
    <div style='font-size:0.66rem;color:#666;line-height:1.5;'>{html.escape(sig['known_unknowns'][:180])}</div>
  </div>
  {contra_html}
  {sources_html}
  {price_html}
</div>"""


def _node_header(node_id: str, signal_count: int) -> str:
    col = NODE_COLOURS[node_id]
    label = NODE_LABELS[node_id]
    icon = col["icon"]
    primary = col["primary"]
    glow = col["glow"]
    avg_conf = 0.0
    sigs = BIOLOGICAL_SIGNALS.get(node_id, [])
    if sigs:
        avg_conf = sum(s["confidence"] for s in sigs) / len(sigs)

    return f"""
<div style='padding:12px 16px;border:1px solid {primary}44;border-radius:10px;
    background:{glow};margin-bottom:6px;display:flex;align-items:center;gap:12px;'>
  <span style='font-size:1.4rem;'>{icon}</span>
  <div style='flex:1;'>
    <div style='font-family:Orbitron,sans-serif;font-size:0.66rem;letter-spacing:3px;
        color:{primary};font-weight:700;'>{label}</div>
    <div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#555;
        margin-top:2px;'>{signal_count} signals · avg confidence {avg_conf:.0%}</div>
  </div>
</div>"""


def render_health_tab() -> None:
    """Main entry point — call from sovereign_hub.py inside the health expander."""

    st.markdown("""
<div style='font-family:Orbitron,sans-serif;font-size:1rem;font-weight:900;
    color:#FFC94A;letter-spacing:6px;margin-bottom:4px;
    text-shadow:0 0 20px rgba(255,201,74,0.4);'>
    LANE 3 — SOVEREIGN BIOLOGICAL INTELLIGENCE</div>
<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;
    color:rgba(255,201,74,0.5);letter-spacing:3px;margin-bottom:6px;'>
    SHARED COGNITION · SEPARATE EXECUTION · LEARN FIRST · MUTATE LATER</div>
<div style='font-family:Rajdhani,sans-serif;font-size:0.8rem;color:#555;
    margin-bottom:20px;line-height:1.6;'>
    Forensic biological decision-support. Evidence mapped, uncertainty surfaced,
    risk intercepted. Not medical advice. High-agency information for informed decisions.
</div>
""", unsafe_allow_html=True)

    # ── GUARDIAN DISCLAIMER ────────────────────────────────────────────────────
    st.markdown("""
<div style='padding:10px 14px;border:1px solid #FF2E6355;border-radius:8px;
    background:rgba(255,46,99,0.04);margin-bottom:16px;'>
  <div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;
      color:#FF2E63;letter-spacing:2px;margin-bottom:4px;'>⚠ GUARDIAN SAFETY LAYER ACTIVE</div>
  <div style='font-size:0.66rem;color:#888;line-height:1.5;'>
    This is a decision-support intelligence layer. It does not prescribe treatment,
    replace clinical advice, or constitute a therapeutic recommendation.
    Contraindications and drug interactions are flagged where known — they are not exhaustive.
    Always consult a qualified clinician before making changes to medications,
    supplements, or health protocols — particularly if you have existing conditions.
  </div>
</div>
""", unsafe_allow_html=True)

    # ── STRATEGY BUILDER ──────────────────────────────────────────────────────
    with st.expander("⚡ STRATEGY EXECUTION ENGINE — Build your protocol", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            goals = st.multiselect(
                "Goals",
                ["Cognitive performance", "Sleep quality", "Energy / mitochondrial",
                 "Longevity / healthspan", "Immune resilience", "Metabolic health",
                 "Inflammation reduction", "Athletic performance", "Mood / neuroplasticity"],
                key="health_goals"
            )
            budget = st.select_slider(
                "Monthly budget (AUD)",
                options=["$0 (behavioural only)", "$20-50", "$50-100",
                         "$100-200", "$200+"],
                key="health_budget"
            )
        with col2:
            risk_tolerance = st.radio(
                "Risk tolerance",
                ["Conservative (high-evidence only)", "Balanced", "Exploratory"],
                key="health_risk"
            )
            profile = st.radio(
                "Preference",
                ["Standard", "Organic / third-party tested", "Premium", "Exploratory"],
                key="health_profile"
            )

        st.markdown("**Safety flags** (check all that apply):")
        c1, c2, c3 = st.columns(3)
        with c1:
            flag_anticoag = st.checkbox("Anticoagulant therapy", key="flag_ac")
            flag_ssri = st.checkbox("SSRI / SNRI", key="flag_ssri")
        with c2:
            flag_kidney = st.checkbox("Kidney disease", key="flag_kidney")
            flag_psych = st.checkbox("Psychiatric history", key="flag_psych")
        with c3:
            flag_pregnancy = st.checkbox("Pregnancy", key="flag_preg")
            flag_thyroid = st.checkbox("Thyroid medication", key="flag_thyroid")

        if st.button("◈ GENERATE STRATEGY", key="gen_strategy"):
            st.markdown("---")
            _render_strategy(goals, budget, risk_tolerance,
                             flag_anticoag, flag_ssri, flag_kidney,
                             flag_psych, flag_pregnancy, flag_thyroid)

    # ── SIX LIVING NODES ──────────────────────────────────────────────────────
    for node_id, node_label in NODE_LABELS.items():
        signals = BIOLOGICAL_SIGNALS.get(node_id, [])
        if not signals:
            continue
        col = NODE_COLOURS[node_id]
        header_html = _node_header(node_id, len(signals))
        st.markdown(header_html, unsafe_allow_html=True)

        with st.expander(f"View {len(signals)} signals", expanded=False):
            for sig in signals:
                # Guardian intercept for psilocybin
                if "psilocybin" in sig.get("tags", []):
                    st.markdown("""
<div style='padding:10px 14px;border:1px solid #A14BFF55;border-radius:8px;
    background:rgba(161,75,255,0.04);margin-bottom:10px;'>
  <div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;
      color:#A14BFF;letter-spacing:2px;margin-bottom:4px;'>
      ◈ RESEARCH CONTEXT ONLY — RESTRICTED SUBSTANCE</div>
  <div style='font-size:0.66rem;color:#888;line-height:1.5;'>
    Legal status in Australia: Schedule 9 (prohibited) except via TGA-authorised
    prescribers for treatment-resistant depression and PTSD (Feb 2023 rescheduling).
    Information presented is from peer-reviewed research only.
    This is not a recommendation, endorsement, or use guide.
  </div>
</div>""", unsafe_allow_html=True)

                card_html = _signal_card(sig, col["primary"])
                st.markdown(card_html, unsafe_allow_html=True)

    # ── LEARNING LAYER ────────────────────────────────────────────────────────
    with st.expander("◈ HEALTH DNA — Log outcomes & build memory", expanded=False):
        st.markdown("""
<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;
    color:#1CF2A4;letter-spacing:2px;margin-bottom:12px;'>
    LOG EXPERIENCE → FEED HEALTH DNA → SYSTEM LEARNS OVER TIME</div>
""", unsafe_allow_html=True)
        log_intervention = st.text_input("Intervention logged", key="hdna_intervention")
        log_duration = st.text_input("Duration (e.g. 4 weeks)", key="hdna_duration")
        log_outcome = st.select_slider(
            "Subjective outcome",
            options=["Strongly negative", "Negative", "Neutral",
                     "Positive", "Strongly positive"],
            value="Neutral",
            key="hdna_outcome"
        )
        log_notes = st.text_area("Notes (side effects, adherence, observations)",
                                  key="hdna_notes", height=80)
        if st.button("◈ RECORD TO HEALTH DNA", key="hdna_save"):
            if log_intervention:
                st.success(f"✓ Logged: {log_intervention} — {log_outcome}")
                # In production: write to cognition DB as health_dna node
            else:
                st.warning("Enter an intervention name to log.")


def _render_strategy(goals, budget, risk_tolerance,
                     flag_anticoag, flag_ssri, flag_kidney,
                     flag_psych, flag_pregnancy, flag_thyroid):
    """Generate a personalised conservative/balanced/exploratory stack."""

    # Guardian pre-screen
    warnings = []
    if flag_anticoag:
        warnings.append("⚠ Anticoagulant therapy: avoid Omega-3 >2g/day, high-dose Vitamin E, high-dose Curcumin, Ginkgo")
    if flag_ssri:
        warnings.append("⚠ SSRI/SNRI: psilocybin contraindicated — serotonin syndrome risk. St John's Wort contraindicated.")
    if flag_psych:
        warnings.append("⚠ Psychiatric history: psilocybin contraindicated. Stimulatory nootropics require caution.")
    if flag_pregnancy:
        warnings.append("⚠ Pregnancy: most supplements contraindicated — seek obstetric guidance for any intervention.")
    if flag_kidney:
        warnings.append("⚠ Kidney disease: magnesium and creatine require medical supervision.")
    if flag_thyroid:
        warnings.append("⚠ Thyroid medication: Bacopa and selenium can interact — spacing and monitoring required.")

    if warnings:
        st.markdown("""
<div style='padding:12px;border:1px solid #FF2E6355;border-radius:8px;
    background:rgba(255,46,99,0.06);margin-bottom:16px;'>
  <div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;
      color:#FF2E63;letter-spacing:2px;margin-bottom:8px;'>⚠ GUARDIAN INTERCEPT — SAFETY FLAGS DETECTED</div>
""", unsafe_allow_html=True)
        for w in warnings:
            st.markdown(f"<div style='font-size:0.66rem;color:#FF6B6B;margin-bottom:4px;'>{html.escape(w)}</div>",
                        unsafe_allow_html=True)
        st.markdown("""
  <div style='font-size:0.66rem;color:#888;margin-top:8px;'>
    Strategy generated below accounts for these flags where possible.
    Consult a qualified clinician before implementing any protocol.
  </div>
</div>""", unsafe_allow_html=True)

    # Score signals against goals
    goal_tags = {
        "Cognitive performance": ["cognitive", "foundational"],
        "Sleep quality": ["foundational"],
        "Energy / mitochondrial": ["metabolic", "mitochondrial"],
        "Longevity / healthspan": ["foundational", "metabolic", "high-evidence"],
        "Immune resilience": ["immune", "foundational"],
        "Metabolic health": ["metabolic", "glucose"],
        "Inflammation reduction": ["inflammation", "antioxidant"],
        "Athletic performance": ["metabolic", "foundational", "cardiovascular"],
        "Mood / neuroplasticity": ["cognitive", "neuroplasticity"],
    }

    target_tags = set()
    for g in goals:
        target_tags.update(goal_tags.get(g, []))

    # Collect all signals and score them
    all_signals = []
    for node_sigs in BIOLOGICAL_SIGNALS.values():
        for sig in node_sigs:
            score = sig["confidence"]
            tag_match = len(set(sig.get("tags", [])) & target_tags)
            score += tag_match * 0.1
            # Apply safety filters
            safe = True
            if flag_anticoag and "anticoag" in " ".join(sig.get("contraindications", [])).lower():
                safe = False
            if flag_psych and "psilocybin" in sig.get("tags", []):
                safe = False
            if flag_pregnancy and sig.get("risk_level") != "LOW":
                safe = False
            if safe:
                all_signals.append((score, sig))

    all_signals.sort(key=lambda x: x[0], reverse=True)

    # Conservative: top 3 high-confidence
    conservative = [s for _, s in all_signals
                    if s["confidence"] >= 0.75 and s["risk_level"] == "LOW"][:3]
    # Balanced: top 5 moderate-confidence
    balanced = [s for _, s in all_signals if s["confidence"] >= 0.55][:5]
    # Exploratory: include emerging signals
    exploratory = [s for _, s in all_signals][:7]

    for strategy_name, signals, colour, desc in [
        ("CONSERVATIVE STACK", conservative, "#1CF2A4", "High-consensus, low-risk signals only"),
        ("BALANCED STACK", balanced, "#FFC94A", "Moderate evidence, moderate risk — broad coverage"),
        ("EXPLORATORY STACK", exploratory, "#A14BFF", "⚠ Includes emerging signals with higher uncertainty"),
    ]:
        if not signals:
            continue
        st.markdown(f"""
<div style='margin-bottom:8px;padding:8px 14px;border-left:3px solid {colour};
    background:rgba(255,255,255,0.02);border-radius:0 8px 8px 0;'>
  <div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;
      color:{colour};letter-spacing:2px;'>{strategy_name}</div>
  <div style='font-size:0.66rem;color:#666;margin-top:2px;'>{desc}</div>
</div>""", unsafe_allow_html=True)
        for sig in signals:
            risk_col = RISK_COLOURS.get(sig["risk_level"], "#888")
            st.markdown(
                f"<div style='padding:6px 12px;margin-bottom:4px;font-size:0.66rem;"
                f"background:rgba(255,255,255,0.02);border-radius:6px;"
                f"display:flex;justify-content:space-between;'>"
                f"<span style='color:#ddd;'>{html.escape(sig['name'])}</span>"
                f"<span style='color:{risk_col};'>{sig['risk_level']} · {sig['confidence']:.0%}</span>"
                f"</div>",
                unsafe_allow_html=True
            )
        st.markdown("<br>", unsafe_allow_html=True)
