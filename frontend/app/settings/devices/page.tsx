import DevicesTab from '@/components/settings/DevicesTab';

export const metadata = {
  title: 'Device Settings',
  description: 'Manage your connected devices and sessions',
};

export default function DevicesPage() {
  return (
    <div className="mx-auto w-full max-w-2xl">
      <h1 className="text-2xl font-semibold text-text-primary mb-6">Devices</h1>
      <DevicesTab />
    </div>
  );
}
