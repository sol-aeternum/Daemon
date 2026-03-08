import ProfileTab from '@/components/settings/ProfileTab';

export const metadata = {
  title: 'Profile Settings',
  description: 'Manage your profile and preferences',
};

export default function ProfilePage() {
  return (
    <div className="max-w-2xl">
      <h1 className="text-2xl font-semibold text-text-primary mb-6">Profile</h1>
      <ProfileTab />
    </div>
  );
}
