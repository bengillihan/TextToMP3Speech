/**
 * Handles polling for conversion progress updates
 */
document.addEventListener('DOMContentLoaded', function() {
    // Find all progress elements
    const progressElements = document.querySelectorAll('[data-conversion-uuid]');
    
    // Set up polling for each progress element
    progressElements.forEach(element => {
        const uuid = element.getAttribute('data-conversion-uuid');
        const progressBar = element.querySelector('.progress-bar');
        const statusBadge = element.querySelector('.status-badge');
        const downloadButton = element.querySelector('.download-btn');
        const cancelButton = element.querySelector('.cancel-btn');
        
        // Only poll for pending or processing conversions
        if (statusBadge && ['pending', 'processing'].includes(statusBadge.getAttribute('data-status'))) {
            pollProgress(uuid, progressBar, statusBadge, downloadButton, cancelButton);
        }
    });
    
    // Set up event listeners for cancel buttons
    const cancelButtons = document.querySelectorAll('.cancel-btn');
    cancelButtons.forEach(button => {
        button.addEventListener('click', function(e) {
            e.preventDefault();
            const uuid = this.getAttribute('data-uuid');
            cancelConversion(uuid, this);
        });
    });
    
    // Set up cleanup button if present
    const cleanupButton = document.getElementById('cleanup-btn');
    if (cleanupButton) {
        cleanupButton.addEventListener('click', function(e) {
            e.preventDefault();
            cleanupFiles(this);
        });
    }
});

/**
 * Polls the server for conversion progress
 */
function pollProgress(uuid, progressBar, statusBadge, downloadButton, cancelButton) {
    // Initial delay before polling starts
    const initialDelay = 1000; // 1 second
    
    // Polling interval
    const pollingInterval = 2000; // 2 seconds
    
    // Start polling after the initial delay
    setTimeout(() => {
        // Function to update the progress
        const updateProgress = () => {
            fetch(`/conversion/${uuid}/progress`)
                .then(response => response.json())
                .then(data => {
                    // Update the progress bar
                    const progress = data.progress;
                    progressBar.style.width = `${progress}%`;
                    progressBar.setAttribute('aria-valuenow', progress);
                    progressBar.textContent = `${Math.round(progress)}%`;
                    
                    // Update the status badge
                    if (statusBadge) {
                        const currentStatus = statusBadge.getAttribute('data-status');
                        const newStatus = data.status;
                        
                        // Only update if the status has changed
                        if (currentStatus !== newStatus) {
                            statusBadge.setAttribute('data-status', newStatus);
                            statusBadge.textContent = newStatus.charAt(0).toUpperCase() + newStatus.slice(1);
                            
                            // Update badge color based on status
                            statusBadge.className = 'badge status-badge';
                            switch (newStatus) {
                                case 'completed':
                                    statusBadge.classList.add('bg-success');
                                    break;
                                case 'processing':
                                    statusBadge.classList.add('bg-primary');
                                    break;
                                case 'pending':
                                    statusBadge.classList.add('bg-warning');
                                    break;
                                case 'failed':
                                    statusBadge.classList.add('bg-danger');
                                    break;
                                case 'cancelled':
                                    statusBadge.classList.add('bg-secondary');
                                    break;
                                default:
                                    statusBadge.classList.add('bg-info');
                            }
                        }
                    }
                    
                    // Show or hide download button based on status
                    if (downloadButton) {
                        if (data.status === 'completed') {
                            downloadButton.classList.remove('d-none');
                        } else {
                            downloadButton.classList.add('d-none');
                        }
                    }
                    
                    // Show or hide cancel button based on status
                    if (cancelButton) {
                        if (['pending', 'processing'].includes(data.status)) {
                            cancelButton.classList.remove('d-none');
                        } else {
                            cancelButton.classList.add('d-none');
                        }
                    }
                    
                    // Continue polling if the conversion is still in progress
                    if (['pending', 'processing'].includes(data.status)) {
                        setTimeout(updateProgress, pollingInterval);
                    } else if (data.status === 'completed') {
                        // Show a success notification
                        showNotification('Conversion complete!', 'Your text has been successfully converted to speech.', 'success');
                    } else if (data.status === 'failed') {
                        // Show an error notification
                        showNotification('Conversion failed', 'There was an error processing your text. Please try again.', 'danger');
                    }
                })
                .catch(error => {
                    console.error('Error fetching progress:', error);
                    // Continue polling even if there was an error
                    setTimeout(updateProgress, pollingInterval);
                });
        };
        
        // Start the polling process
        updateProgress();
    }, initialDelay);
}

/**
 * Cancels a conversion in progress
 */
function cancelConversion(uuid, button) {
    // Disable the button to prevent multiple clicks
    button.disabled = true;
    button.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Cancelling...';
    
    // Send a cancel request to the server
    fetch(`/conversion/${uuid}/cancel`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            showNotification('Error', data.error, 'danger');
            // Re-enable the button
            button.disabled = false;
            button.textContent = 'Cancel';
        } else {
            showNotification('Cancelled', 'Conversion has been cancelled', 'info');
            // Hide the button
            button.classList.add('d-none');
            
            // Update the status badge
            const statusElement = button.closest('[data-conversion-uuid]').querySelector('.status-badge');
            if (statusElement) {
                statusElement.textContent = 'Cancelled';
                statusElement.setAttribute('data-status', 'cancelled');
                statusElement.className = 'badge status-badge bg-secondary';
            }
        }
    })
    .catch(error => {
        console.error('Error cancelling conversion:', error);
        showNotification('Error', 'Failed to cancel conversion', 'danger');
        // Re-enable the button
        button.disabled = false;
        button.textContent = 'Cancel';
    });
}

/**
 * Cleans up old files
 */
function cleanupFiles(button) {
    // Disable the button to prevent multiple clicks
    button.disabled = true;
    button.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Cleaning up...';
    
    // Send a cleanup request to the server
    fetch('/cleanup', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            showNotification('Error', data.error, 'danger');
        } else {
            showNotification('Cleanup Complete', data.message, 'success');
        }
        // Re-enable the button
        button.disabled = false;
        button.textContent = 'Cleanup Old Files';
    })
    .catch(error => {
        console.error('Error cleaning up files:', error);
        showNotification('Error', 'Failed to clean up files', 'danger');
        // Re-enable the button
        button.disabled = false;
        button.textContent = 'Cleanup Old Files';
    });
}

/**
 * Shows a notification to the user
 */
function showNotification(title, message, type = 'info') {
    // Create a toast container if it doesn't exist
    let toastContainer = document.querySelector('.toast-container');
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.className = 'toast-container position-fixed bottom-0 end-0 p-3';
        document.body.appendChild(toastContainer);
    }
    
    // Create the toast element
    const toastId = 'toast-' + Date.now();
    const toast = document.createElement('div');
    toast.className = `toast align-items-center text-white bg-${type} border-0`;
    toast.id = toastId;
    toast.setAttribute('role', 'alert');
    toast.setAttribute('aria-live', 'assertive');
    toast.setAttribute('aria-atomic', 'true');
    
    // Create the toast content
    toast.innerHTML = `
        <div class="d-flex">
            <div class="toast-body">
                <strong>${title}</strong> ${message}
            </div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
        </div>
    `;
    
    // Add the toast to the container
    toastContainer.appendChild(toast);
    
    // Initialize the toast
    const bsToast = new bootstrap.Toast(toast, {
        autohide: true,
        delay: 5000
    });
    
    // Show the toast
    bsToast.show();
    
    // Remove the toast after it's hidden
    toast.addEventListener('hidden.bs.toast', function() {
        toast.remove();
    });
}
