#  ITU Net Assistant - Ultimate Dashboard

İTÜ Gölet Yurtları (ITU Pond Dormitories) gibi ağ bağlantısının sık koptuğu ortamlarda, internet bağlantısını otomatik olarak onaran ve Windows Hotspot'u sürekli aktif tutabilen gelişmiş bir masaüstü aracıdır. 

##  Öne Çıkan Özellikler (Features)

* **Smart Polling (Aktif Yoklama):** Windows NCSI mantığıyla çalışarak IP düşmese bile dış ağa (Port 80) ping atarak gerçek internet erişimini kontrol eder.
* **Otomatik Onarım (Auto-Repair):** Bağlantı koptuğunda Ethernet adaptörünü otomatik olarak resetler (Adapter Reset). 
* **Hotspot Otomasyonu:** İnternet geldikten sonra Windows Hotspot'u PowerShell/Windows Runtime API üzerinden otomatik olarak açar. 
* **Sessiz Başlangıç (Silent Startup):** Windows Görev Zamanlayıcı (Task Scheduler) entegrasyonu ile bilgisayar açıldığında kullanıcıya sormadan arka planda başlar. 
* **Tekil Örnek Koruması (Single Instance Protection):** Windows Mutex kullanarak uygulamanın aynı anda birden fazla kopyasının çalışmasını engeller.
* **Modern Arayüz:** CustomTkinter ile oluşturulmuş, karanlık mod (Dark Mode) destekli dashboard.

## 🛠️ Teknik Detaylar (Technical Details)

Bir bilgisayar mühendisi olarak bu projede aşağıdaki teknolojiler ve yöntemler kullanılmıştır: 

1.  **Threading:** Ağ kontrolleri ve arayüz güncellemeleri, programın donmaması için ayrı iş parçacıklarında (Worker Threads) yürütülür.
2.  **Win32 API:** Pencere ikonlarını ve süreçleri (Process) yönetmek için doğrudan Windows çekirdek kütüphaneleriyle iletişim kurulur.
3.  **Persistence:** Uygulama ayarları `json` formatında saklanır ve `RotatingFileHandler` ile hata kayıtları (Logging) tutulur. 
4.  **IPC (Inter-Process Communication):** Uygulama kopyaları arasındaki çakışmayı önlemek için Mutex mekanizması uygulanmıştır. 

## 📦 Kurulum ve Derleme (Installation & Build)

Projeyi yerel makinenizde çalıştırmak için: 

1. Depoyu Klonlayın (Clone the Repository):
   ```bash
    git clone https://github.com/ossaggelen/ITU_Net_Assistant
    cd ITU_Net_Assistant
    ```

2. Gereksinimleri Yükleyin (Install Dependencies):
    ```bash
    pip install -r requirements.txt
    ```

3. Uygulamayı Paketleyin (Build Executable):
Projeyi .exe haline getirmek için PyInstaller komutunu çalıştırın:

    ```bash
    pyinstaller --noconsole --onefile --uac-admin --icon=icon.ico --add-data "icon.png;." --add-data "icon.ico;." --name "ITUNetAssistant" ITU_Net_Assistant.pyw
    ```
