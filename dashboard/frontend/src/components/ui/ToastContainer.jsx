import { useAlerts } from '../../context/AlertContext';
import Toast from './Toast';

const ToastContainer = () => {
  const { toasts, dismissToast } = useAlerts();

  if (toasts.length === 0) return null;

  return (
    <div className="fixed bottom-5 right-5 z-[200] flex flex-col gap-2">
      {toasts.map((alert) => (
        <Toast key={alert.event_id} alert={alert} onDismiss={dismissToast} />
      ))}
    </div>
  );
};

export default ToastContainer;
