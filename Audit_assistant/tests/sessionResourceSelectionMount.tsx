import { createRoot } from 'react-dom/client';
import { SessionResourceSelection } from '../src/features/investigation/SessionResourceSelection';
createRoot(document.getElementById('root')!).render(<SessionResourceSelection sessionId="selection-test" />);
