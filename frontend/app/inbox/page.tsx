import { notFound } from 'next/navigation';
import { InboxPilot } from './InboxPilot';

export default function InboxPage() {
  // The demo pairs the actual development frontend with the owned loopback
  // fixture. The backend still has to explicitly install the pilot contract.
  const origin = process.env.NEXT_PUBLIC_API_URL;
  if (
    process.env.NODE_ENV !== 'development' ||
    !origin ||
    !/^http:\/\/127\.0\.0\.1:[1-9]\d{0,4}$/.test(origin)
  ) {
    notFound();
  }
  return <InboxPilot endpoint={`${origin}/tasks/inbox-pilot`} />;
}
