# Tanitim sitesi

Tek sayfalik statik site. Harici bagimliligi yok - butun CSS sayfanin icinde,
gorseller `ekranlar/` klasorunde.

## Cloudflare Pages'e yukleme

**Yol 1 - dogrudan yukleme (en hizli):**

1. [dash.cloudflare.com](https://dash.cloudflare.com) → Workers & Pages → Create → Pages
2. "Upload assets" secin, projeye bir isim verin (ornek: `facefold`)
3. Bu `site/` klasorunu surukleyip birakin
4. Deploy → `facefold.pages.dev` adresinde yayinda

**Yol 2 - depoya baglama (her push'ta otomatik guncellenir):**

1. Workers & Pages → Create → Pages → "Connect to Git"
2. `facefold` deposunu secin
3. Build command: **bos birakin**
4. Build output directory: **`site`**
5. Save and Deploy

Kendi alan adinizi baglamak icin: proje → Custom domains → Set up a domain.

## Guncelleme

Ekran goruntuleri `docs/ekranlar/` ile ayni. Yenilerini alirsaniz:

```bash
python docs/yuzleri_bulaniklastir.py docs/ekranlar/*.png
cp docs/ekranlar/*.png site/ekranlar/
```

Test verisi gercek kisilerin bulundugu bir ornek fotograftan uretildigi icin
yuzlerin bulaniklastirilmasi zorunludur.
