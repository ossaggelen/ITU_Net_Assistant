# ITU Net Assistant

İTÜ yurtları gibi ağın sık koptuğu ortamlarda Ethernet bağlantısını otomatik olarak izleyen, koptuğunda adaptörü resetleyerek bağlantıyı tazeleyen ve ardından Windows Mobil Etkin Noktayı (Hotspot) otomatik açan Windows masaüstü aracı.

## Nasıl Çalışır?

1. **Kablo Kontrolü:** Ethernet kablosu takılı değilse gereksiz reset atmaz, kablo takılana kadar pasif modda bekler.
2. **Doğrudan Ethernet Testi:** İnternet kontrolünü doğrudan Ethernet kartının IP adresine soket bağlayarak yapar. Bilgisayarda başka bir ağ (Wi-Fi vb.) açık olsa bile Ethernet'in gerçek durumu izlenir.
3. **Otomatik Adaptör Reset:** Bağlantı kesildiğinde Ethernet adaptörünü devre dışı bırakıp tekrar etkinleştirir ve DHCP'den yeni IP gelene kadar bekler.
4. **Hotspot Otomasyonu:** İnternet bağlantısı geldikten sonra Windows Mobil Etkin Noktayı (Hotspot) arka planda otomatik olarak açar.
5. **Sistem Tepsisi (Tray) & Sessiz Başlangıç:** Kapatıldığında sistem tepsisine (saatin yanına) küçülür. İstenirse Windows açılışında kullanıcıyı rahatsız etmeden arka planda başlayacak şekilde ayarlanabilir.

## Kullanım

Ağ kartını resetleyebilmek için uygulamanın yönetici yetkisiyle çalışması gerekir.
`dist/ITUNetAssistant.exe` (veya derlenen tek dosya exe) doğrudan çalıştırılabilir.

* **Dashboard:** Bağlantı durumunu (Active, Passive, Resetting vb.) canlı gösterir; manuel reset atma, logları açma ve ayarları düzenleme seçenekleri sunar.
* **Ayarlar:** Adaptör adı (varsayılan: `Ethernet`), kontrol aralığı ve başlangıçta otomatik çalışma tercihi `settings.json` dosyasında saklanır, arayüzden değiştirilebilir.

## Kaynak Koddan Çalıştırma ve Derleme

### Geliştirme Ortamı
```bash
git clone https://github.com/ossaggelen/ITU_Net_Assistant.git
cd ITU_Net_Assistant
pip install -r requirements.txt
python ITU_Net_Assistant.pyw
```

### .exe Derleme (Build)
Uygulamayı bağımsız tek bir `.exe` haline getirmek için:

```bash
pyinstaller ITUNetAssistant.spec
```

Çıktı `dist/ITUNetAssistant.exe` konumunda oluşturulur.
