import { useEffect } from 'react';

interface KeyboardShortcutsParams {
  shortcutsModalOpen: boolean;
  setShortcutsModalOpen: React.Dispatch<React.SetStateAction<boolean>>;
  shareModalOpen: boolean;
  setShareModalOpen: React.Dispatch<React.SetStateAction<boolean>>;
  welcomeModalOpen: boolean;
  setWelcomeModalOpen: React.Dispatch<React.SetStateAction<boolean>>;
  dryRunModalOpen: boolean;
  setDryRunModalOpen: React.Dispatch<React.SetStateAction<boolean>>;
  searchOpen: boolean;
  setSearchOpen: React.Dispatch<React.SetStateAction<boolean>>;
  copilotOpen: boolean;
  setCopilotOpen: React.Dispatch<React.SetStateAction<boolean>>;
  standsModalOpen?: boolean;
  setStandsModalOpen?: React.Dispatch<React.SetStateAction<boolean>>;
  accessCodeModalOpen?: boolean;
  setAccessCodeModalOpen?: React.Dispatch<React.SetStateAction<boolean>>;
}

export function useKeyboardShortcuts({
  shortcutsModalOpen,
  setShortcutsModalOpen,
  shareModalOpen,
  setShareModalOpen,
  welcomeModalOpen,
  setWelcomeModalOpen,
  dryRunModalOpen,
  setDryRunModalOpen,
  searchOpen,
  setSearchOpen,
  copilotOpen,
  setCopilotOpen,
  standsModalOpen,
  setStandsModalOpen,
  accessCodeModalOpen,
  setAccessCodeModalOpen,
}: KeyboardShortcutsParams) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const isInput =
        target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.tagName === 'SELECT' ||
          target.isContentEditable);

      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        e.stopPropagation();
        setSearchOpen((o) => !o);
        return;
      }

      if (e.key === '?' && !isInput && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        setShortcutsModalOpen((o) => !o);
        return;
      }

      if (e.key === 'Escape') {
        if (shortcutsModalOpen) {
          e.preventDefault();
          e.stopPropagation();
          setShortcutsModalOpen(false);
        } else if (standsModalOpen && setStandsModalOpen) {
          e.preventDefault();
          e.stopPropagation();
          setStandsModalOpen(false);
        } else if (accessCodeModalOpen && setAccessCodeModalOpen) {
          e.preventDefault();
          e.stopPropagation();
          setAccessCodeModalOpen(false);
        } else if (shareModalOpen) {
          e.preventDefault();
          e.stopPropagation();
          setShareModalOpen(false);
        } else if (welcomeModalOpen) {
          e.preventDefault();
          e.stopPropagation();
          localStorage.setItem('netops_onboarding_done', 'true');
          setWelcomeModalOpen(false);
        } else if (dryRunModalOpen) {
          e.preventDefault();
          e.stopPropagation();
          setDryRunModalOpen(false);
        } else if (searchOpen) {
          e.preventDefault();
          e.stopPropagation();
          setSearchOpen(false);
        } else if (copilotOpen) {
          e.preventDefault();
          e.stopPropagation();
          setCopilotOpen(false);
        }
      }
    };

    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [
    shortcutsModalOpen,
    setShortcutsModalOpen,
    standsModalOpen,
    setStandsModalOpen,
    accessCodeModalOpen,
    setAccessCodeModalOpen,
    shareModalOpen,
    setShareModalOpen,
    welcomeModalOpen,
    setWelcomeModalOpen,
    dryRunModalOpen,
    setDryRunModalOpen,
    searchOpen,
    setSearchOpen,
    copilotOpen,
    setCopilotOpen,
  ]);
}
