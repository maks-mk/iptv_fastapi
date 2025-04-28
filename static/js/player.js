document.addEventListener('DOMContentLoaded', function() {
    const video = document.getElementById('videoPlayer');
    const muteBtn = document.getElementById('muteBtn');
    const fullscreenBtn = document.getElementById('fullscreenBtn');
    const qualityBtn = document.getElementById('qualityBtn');
    const qualitySelect = document.getElementById('qualitySelect');
    
    if (video && muteBtn) {
        muteBtn.addEventListener('click', function() {
            video.muted = !video.muted;
            const icon = muteBtn.querySelector('svg');
            if (video.muted) {
                icon.innerHTML = '<path d="M13 5v14l-7-7H2V9h4l7-7z"></path><path d="M23 9l-6 6"></path><path d="M17 9l6 6"></path>';
            } else {
                icon.innerHTML = '<path d="M13 5v14l-7-7H2V9h4l7-7z"></path><path d="M18 9a6 6 0 0 1 0 8"></path><path d="M21 7a10 10 0 0 1 0 12"></path>';
            }
        });
    }
    
    if (video && fullscreenBtn) {
        fullscreenBtn.addEventListener('click', function() {
            if (document.fullscreenElement) {
                document.exitFullscreen();
            } else {
                video.requestFullscreen().catch(err => {
                    console.error(`Ошибка при входе в полноэкранный режим: ${err.message}`);
                });
            }
        });
    }
    
    if (qualityBtn && qualitySelect) {
        qualityBtn.addEventListener('click', function() {
            qualitySelect.style.display = qualitySelect.style.display === 'none' ? 'block' : 'none';
        });
        
        qualitySelect.addEventListener('change', function() {
            const currentTime = video.currentTime;
            const selectedQuality = qualitySelect.value;
            
            // Пример логики для переключения качества
            // В реальном проекте здесь должна быть логика изменения источника видео
            console.log(`Качество изменено на: ${selectedQuality}`);
            
            // Сохраняем текущую позицию воспроизведения
            video.currentTime = currentTime;
        });
        
        // Скрыть список качества при начальной загрузке
        qualitySelect.style.display = 'none';
    }
}); 