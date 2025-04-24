from PyQt5.QtWidgets import QApplication, QMainWindow, QPushButton, QLabel, QVBoxLayout, QWidget
import sys

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PyTorch Test")
        
        layout = QVBoxLayout()
        self.label = QLabel("Trying to import PyTorch...")
        layout.addWidget(self.label)
        
        button = QPushButton("Try import PyTorch")
        button.clicked.connect(self.try_import)
        layout.addWidget(button)
        
        central_widget = QWidget()
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)
    
    def try_import(self):
        try:
            import torch
            self.label.setText(f"PyTorch imported successfully! Version: {torch.__version__}")
        except Exception as e:
            self.label.setText(f"Error importing PyTorch: {e}")

app = QApplication(sys.argv)
window = MainWindow()
window.show()
sys.exit(app.exec_())