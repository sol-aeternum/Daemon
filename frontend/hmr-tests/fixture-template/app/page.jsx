import Counter from './counter';

export default function Page() {
  return (
    <main>
      <p data-testid="server-version">server-v1</p>
      <Counter />
    </main>
  );
}
