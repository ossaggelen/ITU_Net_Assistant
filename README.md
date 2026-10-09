# ITU Net Assistant

İTÜ Gölet Yurtlarında ethernete bağlıyken bile ağın sık koptuğu ve diğer cihazların bağlanabileceği bir Wi-Fi ağı olmadığı bir ortamda Ethernet bağlantısını otomatik olarak izleyen, koptuğunda adaptörü resetleyerek bağlantıyı tazeleyen ve ardından diğer cihazlarınızın bağlanabilmesi için Windows Mobil İnternet Paylaşımını (Hotspot) otomatik açan ve sürekli açık kalmasını sağlayan Windows masaüstü aracı.

## Nasıl Çalışır?

1. **Kablo Kontrolü:** Ethernet kablosu takılı değilse gereksiz reset atmaz veya hotspot'la ilgilenmez, kablo takılana kadar pasif modda bekler.
2. **Doğrudan Ethernet Testi:** İnternet kontrolünü doğrudan Ethernet kartının IP adresine soket bağlayarak yapar. Bilgisayarda başka bir ağ (Wi-Fi vb.) açık olsa bile Ethernet'in gerçek durumu izlenir.
3. **Otomatik Adaptör Reset:** Bağlantı kesildiğinde Ethernet adaptörünü devre dışı bırakıp tekrar etkinleştirir ve DHCP'den yeni IP gelene kadar bekler. Bunun sonucunda genelde internet bağlantısı tekrar sağlanmış olur.
4. **Hotspot Otomasyonu:** İnternet bağlantısı geldikten sonra Windows Mobil İnternet Paylaşımını (Hotspot) arka planda otomatik olarak açar ve belli aralıklarla açık kalmaya devam edip etmediğini kontrol eder, bir şekilde kapanırsa tekrar açar.
5. **Sistem Tepsisi (Tray) & Sessiz Başlangıç:** Kapatıldığında sistem tepsisine küçülür. İstenirse Windows açılışında kullanıcıyı rahatsız etmeden arka planda başlayacak şekilde ayarlanabilir.

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
