# 🚀 ITU Net Assistant - Ultimate Dashboard

İTÜ Gölet Yurtları (ITU Pond Dormitories) gibi ağ bağlantısının sık koptuğu ortamlarda, internet bağlantısını otomatik olarak onaran ve Windows Hotspot'u sürekli aktif tutabilen gelişmiş bir masaüstü aracıdır. 

## ✨ Öne Çıkan Özellikler (Features)

* **Akıllı İzleme (Smart Monitoring):** İnternet bağlantısını Port 80 üzerinden kontrol ederek firewall engellerini aşar (Port 80 Bypass). 
* **Otomatik Onarım (Auto-Repair):** Bağlantı koptuğunda Ethernet adaptörünü otomatik olarak resetler (Adapter Reset). 
* **Hotspot Otomasyonu:** İnternet geldikten sonra Windows Hotspot'u PowerShell/Windows Runtime API üzerinden otomatik olarak açar. 
* **Sessiz Başlangıç (Silent Startup):** Windows Görev Zamanlayıcı (Task Scheduler) entegrasyonu ile bilgisayar açıldığında kullanıcıya sormadan arka planda başlar. 
* **Tekil Örnek Koruması (Single Instance Protection):** Windows Mutex kullanarak uygulamanın aynı anda birden fazla kopyasının çalışmasını engeller.
* **Modern Arayüz:** CustomTkinter ile oluşturulmuş, karanlık mod (Dark Mode) destekli dashboard.

## 🛠️ Teknik Detaylar (Technical Details)

Bir bilgisayar mühendisi olarak bu projede aşağıdaki teknolojiler ve yöntemler kullanılmıştır: 

1. **Threading:** Ağ kontrolleri ve arayüz güncellemeleri, programın donmaması için ayrı iş parçacıklarında (Worker Threads) yürütülür.
2. **Win32 API:** Pencere ikonlarını ve süreçleri (Process) yönetmek için doğrudan Windows çekirdek kütüphaneleriyle iletişim kurulur.
3. **Persistence:** Uygulama ayarları `json` formatında saklanır ve `RotatingFileHandler` ile hata kayıtları (Logging) tutulur. 
4. **IPC (Inter-Process Communication):** Uygulama kopyaları arasındaki çakışmayı önlemek için Mutex mekanizması uygulanmıştır. 

## 📦 Kurulum ve Derleme (Installation & Build)

Projeyi yerel makinenizde çalıştırmak için: 

1. Depoyu Klonlayın (Clone the Repository):
   ```bash
   git clone https://github.com/ossaggelen/ITU-Net-Assistant.git
   cd ITU-Net-Assistant
   ```

2. Gereksinimleri Yükleyin (Install Dependencies):
   ```bash
   pip install -r requirements.txt
   ```

3. Uygulamayı Paketleyin (Build Executable):
   Projeyi tek ve bağımsız bir `.exe` haline getirmek için PyInstaller ile `.spec` dosyasını derleyin:

   ```bash
   pyinstaller ITUNetAssistant.spec
   ```
