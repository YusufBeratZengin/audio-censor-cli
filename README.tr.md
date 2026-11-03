# audio-censor-cli

**Elindeki altyazıyı kullanarak ses ve videodaki küfürlü sözcükleri bip sesiyle, sessizlikle ya da ters çevrilmiş sesle maskeleyen komut satırı aracı.** Konuşma tanıma modeli yok, videoyu yeniden kodlamak yok.

Elindeki podcast, YouTube çıktısı veya çağrı kaydını; yanında SRT, VTT ya da Whisper JSON dökümüyle birlikte ver. Araç her küfürlü sözcüğü 1 kHz bip, sessizlik veya tersten çalınan ses ile değiştirir. Video akışı bit bit kopyalanır (`-c:v copy`), yani 20 dakikalık 1080p bir dosya dakikalar değil saniyeler içinde biter.

```console
$ audio-censor podcast.mp4 -s podcast.vtt -w badwords.txt --mode bleep -o clean.mp4
info: transcript: podcast.vtt (vtt, 4 cues, 24 words)
info: media: 20.000s, 48000 Hz, 1 ch, video copied
info: 2 match(es) -> 2 masked range(s) in bleep mode
  #1   00:00:09.667 - 00:00:10.083 (0.417s)  'shit'  cue 2
  #2   00:00:15.355 - 00:00:16.258 (0.903s)  'fucking'  cue 3
ok: beeps mixed in: clean.mp4 (0.90s)
```

## Neden bu araç?

Vosk/Whisper tabanlı maskeleyiciler kendi konuşma tanıma modellerini içlerinde taşır: yüzlerce megabayt ağırlık, sistem bağımlılıkları, bazen GPU. Oysa döküm genellikle zaten vardır — YouTube'un otomatik altyazısı, editörün dışa aktardığı SRT, daha önce çalıştırdığın Whisper çıktısı.

Bu araç o dosyayı doğrudan alır, kelime zamanlarını çıkarır, FFmpeg'e sadece ses işini yaptırır ve videoyu olduğu gibi taşır. Çalışma zamanında tek bir Python bağımlılığı yoktur; `re`, `csv`, `wave`, `subprocess` yeter.

## Kurulum

```bash
pip install audio-censor-cli      # Python 3.10+
```

FFmpeg ve FFprobe `PATH` üzerinde olmalı:

| Platform | Komut |
|---|---|
| Debian/Ubuntu | `sudo apt install ffmpeg` |
| macOS | `brew install ffmpeg` |
| Windows | `winget install Gyan.FFmpeg` (veya `choco install ffmpeg`) |

## 30 saniyelik örnek

```bash
cat > badwords.txt <<'EOF'
shit
re:fuck\w*
EOF

audio-censor podcast.mp4 --subtitles podcast.vtt --words badwords.txt --mode bleep -o clean.mp4
```

Aşağıdaki çıktı, `bash examples/build_demo.sh` ile üretilen 20 saniyelik sentetik klipten alınmıştır:

```console
$ audio-censor podcast.mp4 -s podcast.vtt -w badwords.txt --mode bleep -o clean.mp4 \
    --audit audit.csv --clean-subtitles clean.srt
info: transcript: podcast.vtt (vtt, 4 cues, 24 words)
info: media: 20.000s, 48000 Hz, 1 ch, video copied
info: 2 match(es) -> 2 masked range(s) in bleep mode
  #1   00:00:09.667 - 00:00:10.083 (0.417s)  'shit'  cue 2
  #2   00:00:15.355 - 00:00:16.258 (0.903s)  'fucking'  cue 3
info: audit log: audit.csv
info: censored subtitles: clean.srt
ok: beeps mixed in: clean.mp4 (0.90s)
```

Video akışının gerçekten kopyalandığını sıkıştırılmış paketlerin MD5'i ile doğrulayabilirsin:

```console
$ ffmpeg -v error -i podcast.mp4 -map 0:v -c copy -f md5 -
MD5=e8506763944e873c5f880a5cc8b32192
$ ffmpeg -v error -i clean.mp4 -map 0:v -c copy -f md5 -
MD5=e8506763944e873c5f880a5cc8b32192
```

Bip sesi de ölçülebilir: eski kelimenin yerinde 1 kHz, dosyanın geri kalanında orijinal 440 Hz tonu var. Dosyadaki en büyük ardışık örnek farkı 0.039; bu, 0.3 genlikli 1 kHz sinüsün doğal eğimi. Yani tıklama/patlama yok.

## Modlar

| `--mode` | Kelimenin yerine geçen | Ne zaman |
|---|---|---|
| `bleep` (varsayılan) | 1 kHz sinüs, 5 ms giriş/çıkış yumuşatma | YouTube / TV tarzı bip |
| `mute` | tam sessizlik, 5 ms yumuşatma | çağrı merkezi kayıtları, kişisel veri |
| `reverse` | aynı dilimin tersten çalınması, 5 ms yumuşatma | komik efekt, çocuk içerikleri |

Maskeleme aralıklarının dışındaki ses hiç değişmez, orijinal seviyesinde kalır.

## Döküm formatları

| Format | Tanıma | Kelime zamanları |
|---|---|---|
| SubRip (`.srt`) | uzantı veya içerik | replik metni kelimelere bölünür, süre kelime uzunluğuna göre dağıtılır |
| WebVTT (`.vtt`) | uzantı veya `WEBVTT` başlığı | SRT ile aynı |
| Whisper JSON (`.json`) | uzantı veya içerik | `segments[].words[]` içindeki gerçek kelime zamanları aynen kullanılır |

Desteklenen JSON biçimleri: `faster-whisper`/`openai-whisper` (`words` alanlı ya da alansız `segments`), düz `{"words": [...]}`, segment listesi ve `whisper.cpp` çıktısı (`transcription[].offsets`, milisaniye).

SRT ve VTT yalnızca replik düzeyinde zaman taşır; bu yüzden kelime aralıkları uzunluğa göre tahmin edilir. Bu tahmin replik içinde birkaç on milisaniye hassastır — bip için yeterli, cümlenin tamamını gizlemek için değil. Milisaniye hassasiyeti gerekiyorsa Whisper JSON kullan.

## Seçenekler

```
  -s, --subtitles PATH   zaman damgalı döküm (.srt, .vtt, Whisper .json)
  -w, --words SPEC       kelime dosyası ya da "küfür,argo" biçiminde liste
                         (--bad-words takma adı da çalışır)
  -m, --mode {bleep,mute,reverse}
  --beep-freq HZ         bip frekansı (varsayılan 1000)
  --beep-volume V        bip genliği, 0-1 (varsayılan 0.3)
  --fade-ms MS           kenar yumuşatma süresi (varsayılan 5)
  --pad-ms MS            her aralığı iki yana büyütür (varsayılan 0)
  --offset-ms MS         dökümün tamamını kaydırır, negatif olabilir
  --max-gap-ms MS        çok kelimeli ifadede izin verilen en uzun susma
  --fuzzy N              kelime başına N harf hatasına tolerans (varsayılan 0)
  -o, --output PATH      varsayılan: <girdi>.censored.<uzantı>
  --audit PATH           her maskelenen kelime için CSV denetim kaydı
  --clean-subtitles PATH sansürlenmiş altyazı kopyası (clean.srt)
  --mask-text TEXT       --clean-subtitles için yer tutucu (varsayılan [BLEEP])
  --audio-codec / --audio-bitrate   ses kodlayıcı seçimini geçersiz kılar
  --keep-filter-script PATH         üretilen filtre grafiğini saklar
  --dry-run              FFmpeg komutunu yazar, hiçbir dosya üretmez
  --json                 stdout'a makine okunur özet basar
  -q, --quiet            yalnızca hatalar
```

Çıkış kodları: `0` başarılı (eşleşme yoksa dosya olduğu gibi yeniden paketlenir), `1` çalışma hatası, `2` hatalı komut satırı.

## Kelime listesi

Her satır bir girdi, `#` yorum:

```
# yalnızca bariz örnek kelimeler
shit
son of a bitch
re:fuck\w*        # düzenli ifade, re: önekiyle
re:s+h+i+t+       # uzatılmış yazımlar
```

* Düz girdiler **tam kelime** olarak, büyük/küçük harf duyarsız eşleşir; Türkçe `I/İ/ı` ve `ğ/ş/ç/ö/ü` ASCII karşılıklarına katlanır, böylece döküm harfli ya da harfsiz yazılmış olsa da aynı liste tutar. `fuck` yazımı `fucking` ile eşleşmez; kök için `re:fuck\w*` kullan.
* `re:` girdileri normalize edilmiş metin üzerinde çalışan ham Python düzenli ifadeleridir (`re.IGNORECASE`).
* `--fuzzy 1` ek olarak bir harf hatasına tolerans tanır ve tekrarlanan harfleri sadeleştirir; `salllaaak` yazımı `sallak` ile yakalanır.

## Nasıl çalışır?

Kazanç zarfı, tek bir dev `if(between(t,...))` ifadesiyle değil, sabit kaynaklardan kurulur: her aralık `afade` kenarlı bir `aevalsrc=-1` darbesi olur, darbeler toplanıp bir DC kanalla birleşerek 0/1 geçit üretir, `amultiply` bu geçidi ana sese örnek hassasiyetinde uygular. Bip (veya ters dilim) `amix` ile üstüne eklenir.

Bunun sonuçları:

* yumuşatma ses çerçevesine değil örneğe tam oturur, tıklama olmaz;
* FFmpeg'in ~4 KiB ifade sınırına takılmaz, komut satırı da işletim sistemi sınırını aşmaz — grafik her zaman dosyaya yazılıp `-filter_complex_script` (FFmpeg ≥ 7.1'de bu bayrak kaldırıldığı için `-/filter_complex`) ile verilir;
* geniş grafikler sekizerli `amix` bloklarına bölünür, yüzlerce eşleşme tek bir patolojik düğüm üretmez.

`reverse` modu `asplit` kopyasından `atrim` dilimleri alıp `areverse` uygular; bellek dosya uzunluğuyla değil maskelenen konuşmayla orantılıdır.

## Sınırlar (bunu oku)

* **Döküm senkron olmalı.** Her şey bir saniye kaymışsa `--offset-ms -1000` kullan. Araç, döküm medyadan uzun bittiğinde uyarır.
* **Konuşma tanıma yok.** Dökümü üretmek senin işin (Whisper, YouTube otomatik altyazı, Premiere/Resolve dışa aktarımı, elle altyazı).
* **Platform kuralları için garanti yok.** Küfürü maskelemek YouTube'da veya başka bir platformda reklam onayı garantilemez; kurallar değişir ve hem insanlar hem sınıflandırıcılar uygular. Bu araç yalnızca sesi düzenler.
* Video **her zaman** kopyalanır. Hedef konteyner kaynak kodeği taşıyamıyorsa (örneğin H.264'ü `.webm` içine) FFmpeg reddeder; uyumlu bir konteyner seç veya önce remux yap.
* Çok kanallı sesin her kanalı aynı zarfla kısılır. Diyalog/müzik ayrımı (stem) henüz yok.

## Geliştirme

```bash
python3 -m pip install -e ".[dev]"
bash scripts/check.sh        # lint + biçim + tip + test + kapsam eşiği
```

`scripts/check.sh` Ruff, MyPy (strict), pytest çalıştırır ve %85 dal kapsamı ister (şu an ~%94). Araçları etkin yorumlayıcıda, `PATH`'te, `~/.local/bin` içinde ve varsa yerel `.venv`'de arar; bulamadıklarını vazgeçmeden önce kurar, böylece `pip install -e ".[dev]"` çalıştırılmamış taze bir kopyada da çalışır. Bu kurulumu kapatmak için `CHECK_NO_INSTALL=1` verin. Entegrasyon testleri FFmpeg ile gerçek ses üretip 1 kHz bip'i DFT ile doğrular, hiçbir örnek farkının tonun doğal eğimini aşmadığını (tıklama testi) ve video akışı MD5'inin değişmediğini kontrol eder. FFmpeg yoksa otomatik atlanır; test paketinin hiçbiri ağa çıkmaz.

## Katkı ve proje belgeleri

- [CONTRIBUTING.md](CONTRIBUTING.md) — geliştirme kurulumu, `scripts/check.sh` kapısı, kod ve commit kuralları.
- [CHANGELOG.md](CHANGELOG.md) — Keep a Changelog; 0.1.0 ilk sürüm.
- [SECURITY.md](SECURITY.md) — özel güvenlik bildirimi ve tehdit modeli.
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — Contributor Covenant 2.1.
- `good first issue` etiketli konular yaklaşık bir oturumluk; yeni davranış, düzeltme olmadan kırmızıya düşen bir test ister.

## Lisans

MIT © 2026 YusufBeratZengin. Ayrıntı: [LICENSE](LICENSE).
