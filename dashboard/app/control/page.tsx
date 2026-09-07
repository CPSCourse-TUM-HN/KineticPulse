import ScenarioControlPanel from "../components/ScenarioControlPanel";

export const metadata = {
  title: "Scenario control — KineticPulse",
  description: "Bench panel for replaying synthetic PRD scenarios through the live pipeline."
};

export default function ControlPage() {
  return <ScenarioControlPanel />;
}
