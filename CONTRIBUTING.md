# Katkı rehberi

Facefold'a katkı vermek isterseniz teşekkürler. Birkaç kural dışında karmaşık bir süreç yok.

## Kurulum

```bash
python -m pip install -r requirements.txt
python tests/make_fixtures.py
python tests/test_pipeline.py
python tests/test_web.py
python tests/test_dil.py
```

`make_fixtures.py`, insightface ile birlikte gelen örnek grup fotoğrafından altı
kişilik sahte bir aile kütüphanesi üretir: portreler, ikili kareler, kopyalar ve
yüzsüz kareler. Doğru cevap önceden bilindiği için testler gerçek iddialarda
bulunabiliyor.

Testler geçici bir klasörde çalışır (`FACEFOLD_DATA` ortam değişkeni), kendi
kütüphanenize dokunmazlar.

## Kurallar

**Kod İngilizce, arayüz çok dilli.** Değişken ve fonksiyon isimleri İngilizce
olsun ki yabancı geliştiriciler de katkı verebilsin. Kullanıcının gördüğü
metinler şablonun içine yazılmaz, [`facefold/i18n.py`](facefold/i18n.py)
sözlüğüne girer ve şablonda `{{ t('anahtar') }}` ile çağrılır. Yeni bir dil
eklemek o dosyada bir sütun doldurmaktır; yarım bıraktığınız cümleler
İngilizce'ye düşer, bu yüzden tamamlanmamış çeviri de kabul edilir.

**Kişisel veri depoya girmez.** Bu program insanların aile fotoğrafları için
yazıldı; deponun kendisi de aynı özene tabi. Gönderdiğiniz değişiklikte:

- Kod yorumlarına, testlere ve arayüz metinlerine **gerçek kişi adı yazmayın**.
  Örnek kişi olarak `Ayşe Şahin` kullanın.
- Ekran görüntüsü eklerken `python tests/make_fixtures.py` ile üretilen sahte
  kütüphaneyi kullanın — oradaki yüzler bulanıklaştırılmıştır. Görüntüde
  **Windows kullanıcı adınız görünmesin**; programı `C:\Facefold-demo` gibi
  nötr bir klasörden çalıştırın.
- `git status --ignored` ile `veri/` klasörünün dışarıda kaldığını doğrulayın.
  `.gitignore` fotoğraf, video ve arşiv dosyalarını *türüne göre* engeller;
  bir görsel eklemeniz gerekiyorsa bilerek `git add -f` yazmanız gerekir.
- Bir şey yanlışlıkla commit'lendiyse dosyayı silmek yetmez, git geçmişinde
  kalır. Böyle bir durumda issue açın; geçmiş birlikte temizlenir.

**Kaynak dosyalara asla dokunulmaz.** Facefold kullanıcının fotoğraflarını
okur; taşımaz, silmez, yeniden adlandırmaz, üzerine yazmaz. Dosya silen tek yer
`distribute.py` ve orası yalnızca `links` tablosunda kayıtlı **ve** çıktı
klasörünün içinde olan dosyaları silebilir. Bu iki koşulu gevşetmeyin.

**İnternete hiçbir şey gönderilmez.** Modelin ilk indirilmesi dışında program
ağ bağlantısı kurmaz. Telemetri, hata raporlama, güncelleme kontrolü eklemeyin.

**Testler geçmeli.** Yeni bir davranış ekliyorsanız ona bir test yazın.
Eşik değerleri değiştiriyorsanız test kütüphanesinde sonucun neden değiştiğini
açıklayın.

## Aranan katkılar

- **Video desteği** — kareden yüz çıkarma; iPhone Live Photo dosyalarının
  fotoğrafla eşleştirilmesi
- **GPU desteği** — `onnxruntime-gpu` ile çözümleme 10–20 kat hızlanır
- **Yeni diller** — arayüz Türkçe ve İngilizce; `facefold/i18n.py` içine bir
  sütun eklemek yeni bir dil demek. Almanca, Arapça ve İspanyolca en çok işe
  yarayacaklar. Klasör adları da dili izler, çeviride onları da düşünün.
- **macOS ve Linux** — kod taşınabilir yazıldı, `os.link` ve `os.startfile`
  dışında platforma bağlı yer yok, ama yalnızca Windows'ta denendi
- **Klasör seçme penceresi** — yol şu an elle yapıştırılıyor
- **Yaş bilgisini gruplamada kullanma** — çocukların yıllar içinde bölünen
  yüz gruplarını birleştirmeye yardımcı olabilir

## Hata bildirimi

Sorun bildirirken şunları ekleyin: işletim sistemi, Python sürümü, kaç
fotoğrafta olduğu, ve varsa **Ayarlar → İşlenemeyen dosyalar** bölümündeki
hata metni. Fotoğrafınızı göndermeyin — gerekmez.
