// FAQ (facts from the Europress briefing) and method notes, bilingual.
export const FAQ = {
  en: [
    ["How does the machine connect?", "Each machine has a SIM card (4G/5G). Tilt, GPS and movement sensors tell SMART when the container is lifted and transported."],
    ["Does it use a lot of electricity?", "No. The motor is about 3–5 kW and only runs during a compaction cycle — very little compared with the rest of a facility."],
    ["Where does the weight come from?", "From the SMART weight model, which reads the data the machine already sends (fill level, compaction and hydraulic pressure). No extra hardware. It is an approximate weight: on test machines it had never seen, it was about 5 % off on average. A platform scale or Scaletipper gives certified-grade weight where needed."],
    ["How is SMART paid?", "With a share of the money SMART predicts it saves you. The share is agreed with each customer, so you always keep most of the saving. There is no up-front fee."],
    ["Who sees my data?", "You and the partners you agree to — usually your waste operator, so pickups can be planned. Europress never sells data to third parties; data is only exchanged when both parties agree."],
    ["Can technicians fix faults remotely?", "Many — SMART Center connects remotely, sees sensor and electrical states and can solve e.g. blown fuses without a visit."],
    ["What does E-stop mean?", "Emergency stop."],
  ],
  fi: [
    ["Miten laite on yhteydessä verkkoon?", "Jokaisessa laitteessa on SIM-kortti (4G/5G). Kallistus-, GPS- ja liikeanturit kertovat SMARTille, milloin kontti nostetaan ja kuljetetaan."],
    ["Kuluttaako laite paljon sähköä?", "Ei. Moottori on noin 3–5 kW ja käy vain puristusjakson aikana — hyvin vähän verrattuna kiinteistön muuhun kulutukseen."],
    ["Mistä painotieto tulee?", "SMART-painomallista, joka lukee laitteen jo lähettämää dataa (täyttöaste, puristus ja hydraulipaine). Lisälaitteita ei tarvita. Paino on likimääräinen: testilaitteilla, joita malli ei ollut nähnyt, se oli keskimäärin noin 5 % pielessä. Lavavaaka tai Scaletipper antaa tarkan painon, kun sitä tarvitaan."],
    ["Miten SMART maksetaan?", "Osuudella siitä rahasta, jonka SMART ennustaa säästävänsä sinulle. Osuus sovitaan asiakaskohtaisesti, joten suurin osa säästöstä jää aina sinulle. Aloitusmaksua ei ole."],
    ["Kuka näkee tietoni?", "Sinä ja kumppanit, joista sovit — yleensä jäteoperaattorisi, jotta tyhjennykset voidaan suunnitella. Europress ei koskaan myy dataa kolmansille osapuolille; tietoja vaihdetaan vain, kun molemmat osapuolet hyväksyvät sen."],
    ["Voiko vikoja korjata etänä?", "Monia — SMART Center ottaa etäyhteyden, näkee anturien ja sähköjen tilan ja voi ratkaista esim. palaneet sulakkeet ilman käyntiä."],
    ["Mitä E-stop tarkoittaa?", "Hätäpysäytys."],
  ],
};

export const METHOD = {
  en: [
    ["Data", "Synthetic Europress competition pack: 52 customers, 447 machines, 12 months (Oct 2025 – Sep 2026), contracts, telemetry, and billing + weighbridge data shared by the waste-operator partners (customers never upload invoices). All prices are hypothetical."],
    ["Savings levers", "Three levers, each clickable: (1) empty at 85 % fill instead of earlier — biowaste and max-interval pickups excluded; (2) 50 % of penalty and extra fees avoided; (3) 20 % of mixed waste moved to recyclable streams."],
    ["Recycling rate", "Tonnes of cardboard, paper, plastics and biowaste ÷ all tonnes. Mixed waste is the only non-recycled stream in the data."],
    ["CO₂", "Truck trips × billable round-trip km × 1.1 kg CO₂/km (diesel refuse truck, assumption)."],
    ["Operators", "Your waste operators are SMART partners. The Contracts page shows the on-time rate and order-to-pickup time of pickups ordered through SMART — service information, not a verdict."],
    ["AI advisor", "Claude (Anthropic) runs on the Europress server with read-only access to the same data files; it cannot browse the web or change anything. It always names the file it used."],
  ],
  fi: [
    ["Data", "Synteettinen Europress-kilpailuaineisto: 52 asiakasta, 447 laitetta, 12 kk (loka 2025 – syys 2026), sopimukset, telemetria sekä jäteoperaattorikumppanien jakamat laskutus- ja punnitustiedot (asiakkaat eivät koskaan lähetä laskuja). Kaikki hinnat ovat kuvitteellisia."],
    ["Säästövivut", "Kolme vipua, jokainen avattavissa: (1) tyhjennys 85 % täyttöasteella aiemman sijaan — biojäte ja enimmäisvälin tyhjennykset pois lukien; (2) 50 % sanktioista ja lisämaksuista vältetään; (3) 20 % sekajätteestä siirtyy kierrätysjakeisiin."],
    ["Kierrätysaste", "Kartongin, paperin, muovien ja biojätteen tonnit ÷ kaikki tonnit. Sekajäte on aineiston ainoa kierrättämätön jae."],
    ["CO₂", "Kuorma-autokäynnit × laskutettava edestakainen matka × 1,1 kg CO₂/km (dieselkäyttöinen jäteauto, oletus)."],
    ["Operaattorit", "Jäteoperaattorisi ovat SMART-kumppaneita. Sopimukset-sivu näyttää SMARTin kautta tilattujen tyhjennysten ajallaan-osuuden ja tilauksesta tyhjennykseen kuluneen ajan — palvelutietoa, ei tuomio."],
    ["Tekoälyneuvoja", "Claude (Anthropic) toimii Europressin palvelimella ja sillä on vain lukuoikeus samoihin datatiedostoihin; se ei voi selata verkkoa eikä muuttaa mitään. Se kertoo aina, mitä tiedostoa käytti."],
  ],
};
